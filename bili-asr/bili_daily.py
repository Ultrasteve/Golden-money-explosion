#!/usr/bin/env python3
"""Termux/Python 3: 视频组日调度 —— 每组每天只取"最快发布"的那个 UP 的新视频，转写后按组名归档。

配置见 groups.example.json。依赖 yt-dlp（取 UP 主投稿扁平列表）与 bili_asr.py 的下载/转写流程。
只处理你有权下载的视频。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time

from bili_asr import (BVID_RE, atomic_text, convert_wav, download_audio, format_srt,
                      timestamp, transcribe_wav)

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
UNSAFE_RE = re.compile(r'[/\\:*?"<>|]')


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
               cookie_file: Path | None,
               insecure: bool) -> tuple[dict | None, list[str], int]:
    """返回 (当日该组应处理的视频, 过程说明, 取数失败的 UP 数)。命中多条时取发布最早的那条。

    扁平列表按发布时间倒序，且通常不带标题/时间，所以从最新往回逐条补查；
    一旦查到早于当天的稿件就停（更下面的只会更早），每组每个 UP 最多补查 probe_limit 条。
    """
    pattern = re.compile(group.get("title_pattern") or ".")
    day_start = time.mktime(time.strptime(day, "%Y-%m-%d"))
    day_end = day_start + 86400
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
        for entry in entries:
            bvid = entry_bvid(entry)
            if not bvid:
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
        if matched == 0:
            notes.append(f"UP {alias}: 当日无命中投稿（补查 {probed} 条）")
    if not found:
        return None, notes, broken
    return min(found, key=lambda item: item["ts"]), notes, broken


def run_one(picked: dict, group_dir: Path, work_dir: Path, opts: dict, day: str) -> Path:
    """复用 bili_asr 的单视频流程，产物按 <日期> 落在组目录下。"""
    bvid = picked["bvid"]
    media = work_dir / "media"
    chunks = work_dir / "chunks"
    for directory in (media, chunks, group_dir):
        directory.mkdir(parents=True, exist_ok=True)
    out_txt = group_dir / f"{day}.txt"
    out_srt = group_dir / f"{day}.srt"

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
              "时间索引为分块起点；SRT 优先使用模型返回的语音段边界，不保证逐词精度。"]
    body = [f"[{timestamp(d['start_frame'] * 1000 // 16000).split(',')[0]}] {d['text']}"
            for d in data if d["text"]]
    atomic_text(out_txt, "\n".join(header + [""] + body) + "\n")
    atomic_text(out_srt, format_srt(data))
    atomic_text(group_dir / f"{day}.metadata.json",
                json.dumps({"group": group_dir.name, "date": day, "bvid": bvid,
                            "up_alias": picked["alias"], "up_uid": picked["uid"],
                            "published_at": time.strftime("%Y-%m-%d %H:%M:%S",
                                                           time.localtime(picked["ts"])),
                            "title": final_title, "audio": str(audio),
                            "model": "iic/SenseVoiceSmall", "language": opts["language"],
                            "timing_methods": [d["timing_method"] for d in data]},
                           ensure_ascii=False, indent=2))
    return out_txt


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
                        help="今日已处理的组也重跑，并忽略片段转录缓存")
    parser.add_argument("--cookies-file", type=Path, help="覆盖配置里的 cookie_file")
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
        "model_dir": str(Path(cfg["model_dir"]).expanduser()) if cfg.get("model_dir") else None,
        "vad_dir": str(Path(cfg["vad_dir"]).expanduser()) if cfg.get("vad_dir") else None,
    }
    recent_limit = int(cfg.get("recent_limit", 30))
    probe_limit = int(cfg.get("probe_limit", 12))
    log = DailyLog(root / "logs" / f"{day}.log")
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
            if not args.force and log.done_today(name) and (group_dir / f"{day}.srt").is_file():
                log.write("SKIP", name, f"今日已处理过，--force 可重跑（{group_dir / (day + '.srt')}）")
                continue
            picked, notes, broken = pick_today(group, day,
                                               int(group.get("recent_limit") or recent_limit),
                                               int(group.get("probe_limit") or probe_limit),
                                               opts["cookie_file"], opts["insecure"])
            for note in notes:
                log.write("INFO", name, note)
            ups_total = len(group.get("ups") or [])
            if not picked and ups_total and broken == ups_total:
                failures += 1
                log.write("FAILED", name, f"{broken} 个 UP 的投稿列表都没取到，见上方 INFO")
                continue
            if not picked:
                log.write("SKIP", name, "今日无命中的新视频")
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
