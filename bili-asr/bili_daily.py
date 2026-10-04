#!/usr/bin/env python3
"""Termux/Python 3: 视频组日调度 —— 每组每天只取"最快发布"的那个 UP 的新视频，转写后按组名归档。

产物只保留 <组名>/<日期>.txt；成功后自动清掉 work/ 的下载音频与分块缓存，srt/metadata 不再写盘。
配置见 groups.example.json。依赖 yt-dlp（取 UP 主投稿扁平列表）与 bili_asr.py 的下载/转写流程。
只处理你有权下载的视频。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import sys
import time

from bili_asr import (BVID_RE, atomic_text, convert_wav, download_audio,
                      timestamp, transcribe_wav)

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
UNSAFE_RE = re.compile(r'[/\\:*?"<>|]')


def model_path(value: object) -> str | None:
    """模型目录可为空（留空则联网拉权重），给了值就展开 ~ 成绝对路径。"""
    text = str(Path(str(value or "")).expanduser()).strip()
    return text or None


def safe_dirname(name: str) -> str:
    cleaned = UNSAFE_RE.sub("_", name).strip().rstrip(".")
    if not cleaned:
        raise RuntimeError(f"组名不能清理为空：{name!r}")
    return cleaned


def load_config(path: Path) -> dict:
    if not path.is_file():
        raise RuntimeError(f"找不到配置文件：{path}（可先 cp groups.example.json groups.json）")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cfg.get("groups"), list) or not cfg["groups"]:
        raise RuntimeError(f"配置里 groups 不能为空：{path}")
    return cfg


def entry_bvid(entry: dict) -> str:
    raw = str(entry.get("id") or entry.get("url") or "").strip()
    raw = raw.split("?", 1)[0].rsplit("/", 1)[-1]
    return raw if BVID_RE.fullmatch(raw) else ""


def entry_time(entry: dict) -> float:
    """扁平条目的发布时间：多数 yt-dlp 版本的 BilibiliSpaceVideoIE 不给时间，返回 0 由上层补查。"""
    for key in ("timestamp", "release_timestamp"):
        value = entry.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    upload_date = str(entry.get("upload_date") or "")
    if re.fullmatch(r"\d{8}", upload_date):
        return time.mktime(time.strptime(upload_date, "%Y%m%d"))
    return 0.0


def ydl_base_opts(cookie_file: Path | None, insecure: bool) -> dict:
    opts = {"skip_download": True, "quiet": True, "no_warnings": True,
            "noprogress": True, "socket_timeout": 25, "retries": 2,
            "nocheckcertificate": insecure}
    if cookie_file:
        if not cookie_file.is_file():
            raise RuntimeError(f"Cookie 文件不存在：{cookie_file}")
        opts["cookiefile"] = str(cookie_file)
    return opts


def _ydl(cookie_file: Path | None, insecure: bool, extra: dict | None = None):
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise RuntimeError("缺少 yt-dlp，请先 pip install yt-dlp") from exc
    opts = ydl_base_opts(cookie_file, insecure)
    opts.update(extra or {})
    return YoutubeDL(opts)


def list_uploads(uid: str, limit: int, cookie_file: Path | None, insecure: bool) -> list[dict]:
    """取 UP 主投稿扁平列表（按发布时间倒序）。只保证有 bvid，标题/时间可能要逐条补查。"""
    url = f"https://space.bilibili.com/{uid}/video"
    with _ydl(cookie_file, insecure,
              {"extract_flat": "in_playlist", "playlist_items": f"1-{max(1, limit)}"}) as ydl:
        info = ydl.extract_info(url, download=False)
    return [e for e in ((info or {}).get("entries") or []) if isinstance(e, dict)]


def video_info(bvid: str, cookie_file: Path | None, insecure: bool) -> tuple[str, float]:
    """单条稿件的标题与发布时间（秒）。BiliBiliIE 的 timestamp 来自 pubdate。"""
    with _ydl(cookie_file, insecure) as ydl:
        info = ydl.extract_info(f"https://www.bilibili.com/video/{bvid}", download=False)
    if not isinstance(info, dict):
        return "", 0.0
    return str(info.get("title") or ""), entry_time(info)


def pick_today(group: dict, day: str, recent_limit: int, probe_limit: int,
               cookie_file: Path | None, insecure: bool,
               processed: dict[str, dict] | None = None
               ) -> tuple[dict | None, list[dict], list[str], int]:
    """返回 (应处理的那条, 全部候选, 过程说明, 取数失败的 UP 数)。命中多条时取发布最早的那条。

    扁平列表按发布时间倒序，且通常不带标题/时间，所以从最新往回逐条补查；
    一旦查到早于当天的稿件就停（更下面的只会更早），每组每个 UP 最多补查 probe_limit 条。
    processed 是"处理过的 BV"台账：命中的条目直接跳过、不再补查（时间取台账里的）。
    """
    pattern = re.compile(group.get("title_pattern") or ".")
    day_start = time.mktime(time.strptime(day, "%Y-%m-%d"))
    day_end = day_start + 86400
    processed = processed or {}
    notes: list[str] = []
    found: list[dict] = []
    broken = 0
    for up in group.get("ups") or []:
        uid = str(up.get("uid") or "").strip()
        alias = str(up.get("alias") or uid)
        if not uid.isdigit():
            broken += 1
            notes.append(f"UP {alias}: UID 不是数字，跳过")
            continue
        try:
            entries = list_uploads(uid, recent_limit, cookie_file, insecure)
        except Exception as exc:
            broken += 1
            notes.append(f"UP {alias}: 拉投稿列表失败（{type(exc).__name__}: {str(exc)[:150]}）")
            continue
        if not entries:
            broken += 1
            notes.append(f"UP {alias}: 投稿列表为空（可能需 Cookie 或已被风控）")
            continue
        probed = 0
        matched = 0
        undated = 0
        seen = 0
        for entry in entries:
            bvid = entry_bvid(entry)
            if not bvid:
                continue
            rec = processed.get(bvid)
            if rec:
                seen += 1
                stamp = entry_time(entry) or float(rec.get("ts") or 0)
                if stamp and stamp < day_start:
                    break
                continue
            title = str(entry.get("title") or "")
            ts = entry_time(entry)
            if not ts or not title:
                if probed >= probe_limit:
                    notes.append(f"UP {alias}: 补查达到上限 {probe_limit} 条，停止")
                    break
                probed += 1
                try:
                    probed_title, probed_ts = video_info(bvid, cookie_file, insecure)
                except Exception as exc:
                    notes.append(f"UP {alias}: {bvid} 补查稿件信息失败"
                                 f"（{type(exc).__name__}: {str(exc)[:120]}）")
                    continue
                title = title or probed_title
                ts = ts or probed_ts
            if not ts:
                undated += 1
                continue
            if ts < day_start:
                break
            if ts >= day_end:
                notes.append(f"UP {alias}: {bvid} 发布时间晚于 {day}，忽略")
                continue
            if not pattern.search(title):
                notes.append(f"UP {alias}: {bvid} 标题未命中正则，忽略（{title[:40]}）")
                continue
            matched += 1
            found.append({"bvid": bvid, "title": title, "uid": uid, "alias": alias, "ts": ts})
        if undated:
            notes.append(f"UP {alias}: {undated} 条稿件查不到发布时间，无法判定")
        if seen:
            notes.append(f"UP {alias}: {seen} 条已在处理台账中，跳过")
        if matched == 0:
            notes.append(f"UP {alias}: 当日无命中投稿（补查 {probed} 条）")
    found.sort(key=lambda item: item["ts"])
    return (found[0] if found else None), found, notes, broken


def run_one(picked: dict, group_dir: Path, work_dir: Path, opts: dict, day: str) -> Path:
    """复用 bili_asr 的单视频流程，产物按 <日期> 落在组目录下。

    成功后只保留 <日期>.txt：srt/metadata 不再写盘，work/ 下的下载音频与
    分块缓存整个删掉；中途失败则原样保留，供断点续跑。
    """
    bvid = picked["bvid"]
    media = work_dir / "media"
    chunks = work_dir / "chunks"
    for directory in (media, chunks, group_dir):
        directory.mkdir(parents=True, exist_ok=True)
    out_txt = group_dir / f"{day}.txt"

    audio, title = download_audio(bvid, media, opts["insecure"], None,
                                 opts["cookie_file"], opts["resume_download"])
    wav_file = media / (bvid + "-16k.wav")
    convert_wav(audio, wav_file)
    data = transcribe_wav(wav_file, chunks, opts["chunk_seconds"], opts["language"],
                         opts["force"], opts["model_dir"], opts["vad_dir"])
    final_title = title or picked["title"]
    header = [f"视频组：{group_dir.name}", f"日期：{day}",
              f"UP：{picked['alias']}（UID {picked['uid']}）",
              f"BV：{bvid}", f"来源：https://www.bilibili.com/video/{bvid}",
              f"标题：{final_title}",
              "时间索引为分块起点，优先使用模型返回的语音段边界，不保证逐词精度。"]
    body = [f"[{timestamp(d['start_frame'] * 1000 // 16000).split(',')[0]}] {d['text']}"
            for d in data if d["text"]]
    atomic_text(out_txt, "\n".join(header + [""] + body) + "\n")
    shutil.rmtree(work_dir, ignore_errors=True)
    for stale in (group_dir / f"{day}.srt", group_dir / f"{day}.metadata.json"):
        stale.unlink(missing_ok=True)
    return out_txt


class Ledger:
    """processed.json：处理成功的 BV 永久台账，跨天跨组都不再重复处理（--force 才绕过）。"""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.data: dict[str, dict] = {}
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                loaded = None
            if isinstance(loaded, dict) and isinstance(loaded.get("bvids"), dict):
                self.data = {k: v for k, v in loaded["bvids"].items() if isinstance(v, dict)}

    def mark(self, bvid: str, record: dict) -> None:
        self.data[bvid] = record
        atomic_text(self.path, json.dumps({"bvids": self.data}, ensure_ascii=False, indent=2))


class DailyLog:
    """logs/<日期>.log 既是运行日志，也是"今日该组是否已处理"的依据。"""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = path.open("a", encoding="utf-8")

    def write(self, status: str, group: str, message: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {status} | {group} | {message}"
        print(line, flush=True)
        self.fh.write(line + "\n")
        self.fh.flush()

    def done_today(self, group: str) -> bool:
        if not self.path.is_file():
            return False
        prefix = time.strftime("%Y-%m-%d")
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.startswith(prefix) and f" | OK | {group} | " in line:
                return True
        return False

    def close(self) -> None:
        self.fh.close()


def main() -> None:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="按视频组每日调度：取今日最快发布的视频并转写")
    parser.add_argument("--config", type=Path, default=here / "groups.json")
    parser.add_argument("--output", type=Path, help="覆盖配置里的 output_root")
    parser.add_argument("--group", action="append", help="只跑指定组名，可重复")
    parser.add_argument("--date", help="处理指定日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--dry-run", action="store_true", help="只选视频，不下载不转写")
    parser.add_argument("--force", action="store_true",
                        help="破除全部去重：今日已处理的组重跑、处理台账里的 BV 重新入选，并忽略片段转录缓存")
    parser.add_argument("--cookies-file", type=Path, help="覆盖配置里的 cookie_file")
    parser.add_argument("--model-dir", type=Path, help="覆盖配置里的 model_dir（本地 SenseVoiceSmall 目录）")
    parser.add_argument("--vad-dir", type=Path, help="覆盖配置里的 vad_dir（本地 fsmn-vad 目录）")
    parser.add_argument("--resume-download", action="store_true", help="允许 yt-dlp 断点续传")
    args = parser.parse_args()

    if args.date and not DATE_RE.fullmatch(args.date):
        parser.error("--date 应为 YYYY-MM-DD")
    cfg = load_config(args.config.expanduser().resolve())
    day = args.date or time.strftime("%Y-%m-%d")
    root = (args.output or Path(cfg.get("output_root") or (here / "bili-daily"))).expanduser().resolve()
    cookie = args.cookies_file or cfg.get("cookie_file")
    opts = {
        "insecure": bool(cfg.get("insecure", True)),
        "language": cfg.get("language", "auto"),
        "chunk_seconds": int(cfg.get("chunk_seconds", 60)),
        "cookie_file": Path(cookie).expanduser().resolve() if cookie else None,
        "force": args.force,
        "resume_download": args.resume_download,
        "model_dir": model_path(args.model_dir or cfg.get("model_dir")),
        "vad_dir": model_path(args.vad_dir or cfg.get("vad_dir")),
    }
    recent_limit = int(cfg.get("recent_limit", 30))
    probe_limit = int(cfg.get("probe_limit", 12))
    log = DailyLog(root / "logs" / f"{day}.log")
    ledger = Ledger(root / "processed.json")
    wanted = set(args.group) if args.group else None
    failures = 0
    try:
        for group in cfg["groups"]:
            name = str(group.get("name") or "").strip()
            if not name:
                failures += 1
                log.write("FAILED", "(缺 name)", "该组没有 name 字段")
                continue
            if wanted and name not in wanted:
                continue
            try:
                dir_name = safe_dirname(name)
            except RuntimeError as exc:
                failures += 1
                log.write("FAILED", name, str(exc))
                continue
            group_dir = root / "results" / dir_name
            if not args.force and log.done_today(name) and (group_dir / f"{day}.txt").is_file():
                log.write("SKIP", name, f"今日已处理过，--force 可重跑（{group_dir / (day + '.txt')}）")
                continue
            picked, candidates, notes, broken = pick_today(
                group, day,
                int(group.get("recent_limit") or recent_limit),
                int(group.get("probe_limit") or probe_limit),
                opts["cookie_file"], opts["insecure"],
                None if args.force else ledger.data)
            for note in notes:
                log.write("INFO", name, note)
            for cand in candidates[1:]:
                log.write("INFO", name, "同日还有 "
                            f"{cand['bvid']} UP={cand['alias']} "
                            f"发布={time.strftime('%H:%M', time.localtime(cand['ts']))}，更晚所以不处理")
            ups_total = len(group.get("ups") or [])
            if not picked and ups_total and broken == ups_total:
                failures += 1
                log.write("FAILED", name, f"{broken} 个 UP 的投稿列表都没取到，见上方 INFO")
                continue
            if not picked:
                log.write("SKIP", name, "当日无命中的新视频")
                continue
            published = time.strftime("%H:%M", time.localtime(picked["ts"]))
            if args.dry_run:
                log.write("DRY", name, f"将处理 {picked['bvid']} UP={picked['alias']} "
                                       f"发布={published} 标题={picked['title'][:40]}")
                continue
            try:
                out_txt = run_one(picked, group_dir, root / "work" / dir_name / day, opts, day)
            except Exception as exc:
                failures += 1
                log.write("FAILED", name, f"{picked['bvid']} {type(exc).__name__}: {str(exc)[:300]}")
                continue
            log.write("OK", name, f"{picked['bvid']} UP={picked['alias']} 发布={published} → {out_txt}")
            ledger.mark(picked["bvid"], {
                "group": name, "date": day, "bvid": picked["bvid"],
                "title": picked["title"], "uid": picked["uid"], "alias": picked["alias"],
                "ts": picked["ts"],
                "done_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    finally:
        log.close()
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        print(f"失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
