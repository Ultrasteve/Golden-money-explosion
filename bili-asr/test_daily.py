#!/usr/bin/env python3
"""bili_daily.py 的离线回归测试：伪造 yt-dlp 投稿列表与转录结果，不联网、不加载模型。

用法：python test_daily.py
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))

DAY = "2026-10-02"
NOON = time.mktime(time.strptime(DAY + " 12:00:00", "%Y-%m-%d %H:%M:%S"))


def ts(hh: int, mm: int, dd: int = 0) -> float:
    return NOON + (hh - 12) * 3600 + mm * 60 + dd * 86400


YDAY = "BV1YESTERDA0"     # 前一天 20:00，标题命中
LATE = "BV1LATE00001"     # 当天 09:30，标题命中（较晚）
TRAILER = "BV1TRAILER01"  # 当天 07:15，标题不命中
EARLY = "BV1EARLY0001"    # 当天 06:00，标题命中（应被选中）
OLD = "BV1OLDDAY001"      # 三天前

from bili_asr import BVID_RE  # noqa: E402

for _bvid in (YDAY, LATE, TRAILER, EARLY, OLD):
    assert BVID_RE.fullmatch(_bvid), f"夹具用的 BV 号不合法: {_bvid}"

FIXTURES = {
    "111": [{"id": LATE, "title": "今日盘面复盘 0930", "timestamp": ts(9, 30)},
            {"id": TRAILER, "title": "今晚直播预告", "timestamp": ts(7, 15)},
            {"id": YDAY, "title": "昨日复盘", "timestamp": ts(20, 0, -1)}],
    "222": [{"id": EARLY, "title": "早盘复盘 0600", "timestamp": ts(6, 0)}],
    "333": "RAISE",
    "444": [{"id": OLD, "title": "复盘（旧）", "timestamp": ts(9, 0, -3)}],
}


class FakeYoutubeDL:
    """替身：只按 uid 返回预置投稿条目，并检查调用参数符合扁平列表用法。"""

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        assert download is False, "取列表不应下载"
        assert self.opts.get("extract_flat") == "in_playlist"
        data = FIXTURES[url.split("/")[-2]]
        if data == "RAISE":
            raise RuntimeError("HTTP Error 412: Precondition Failed")
        limit = int(self.opts["playlist_items"].split("-")[1])
        return {"entries": [dict(entry) for entry in data[:limit]]}


fake_module = type(sys)("yt_dlp")
fake_module.YoutubeDL = FakeYoutubeDL
sys.modules["yt_dlp"] = fake_module

import bili_daily  # noqa: E402

CALLS: list[str] = []


def fake_download_audio(bvid, media_dir, insecure, browser, cookies_file, resume):
    path = media_dir / (bvid + ".m4a")
    path.write_bytes(b"x")
    CALLS.append(bvid)
    return path, "下载器标题 " + bvid


bili_daily.download_audio = fake_download_audio
bili_daily.convert_wav = lambda source, wav_file: wav_file.write_bytes(b"w")
bili_daily.transcribe_wav = lambda wav_file, chunks, sec, lang, force, m, v: [
    {"text": "转写正文", "entries": [{"start_ms": 0, "end_ms": 5000, "text": "转写正文"}],
     "timing_method": "vad_or_sentence_boundaries", "start_frame": 0, "frames": 80000,
     "language": lang}]

TMP = Path(tempfile.mkdtemp(prefix="bili-daily-test-"))
CFG = {
    "output_root": str(TMP / "out"), "insecure": True, "language": "zh",
    "chunk_seconds": 60, "recent_limit": 5,
    "groups": [
        {"name": "A组/异常:字符", "title_pattern": "复盘", "ups": [
            {"uid": "111", "alias": "UP甲"}, {"uid": "222", "alias": "UP乙"},
            {"uid": "333", "alias": "UP丙"}]},
        {"name": "B组", "title_pattern": "复盘", "ups": [{"uid": "444", "alias": "UP丁"}]},
        {"name": "C组全失败", "title_pattern": ".", "ups": [{"uid": "333", "alias": "UP丙"}]},
    ],
}
(TMP / "groups.json").write_text(json.dumps(CFG, ensure_ascii=False), encoding="utf-8")

ROOT = TMP / "out"
RESULTS = ROOT / "results"
LOG = ROOT / "logs" / f"{DAY}.log"
FAILED_CHECKS: list[str] = []


def listing(path: Path) -> list[str]:
    return sorted(p.name for p in path.iterdir()) if path.is_dir() else ["<目录不存在>"]


def check(label: str, cond: bool, detail: object = "") -> None:
    print(("PASS  " if cond else "FAIL  ") + label + ("" if cond else f"   >> {detail}"))
    if not cond:
        FAILED_CHECKS.append(label)


def run(extra: list[str] | None = None) -> int:
    sys.argv = ["bili_daily.py", "--config", str(TMP / "groups.json"), "--date", DAY] + (extra or [])
    try:
        bili_daily.main()
        return 0
    except SystemExit as exc:
        return exc.code or 0


def log_text() -> str:
    return LOG.read_text(encoding="utf-8") if LOG.is_file() else ""


code = run()
group_dir = RESULTS / "A组_异常_字符"
txt, srt = group_dir / f"{DAY}.txt", group_dir / f"{DAY}.srt"
body = txt.read_text(encoding="utf-8") if txt.is_file() else ""

check("有组全失败时退出码为 1", code == 1, code)
check("组名做目录且非法字符换成下划线", group_dir.is_dir(), listing(RESULTS))
check("输出文件按日期命名", srt.is_file() and txt.is_file(), listing(group_dir))
check("选中的是当日最早命中的一条（UP乙 06:00）", EARLY in body and "UP乙" in body, body)
check("同日较晚命中的 UP甲 未被选", LATE not in body)
check("标题未命中正则的被忽略", TRAILER not in body and "未命中正则" in log_text())
check("前一天的投稿不算今日", YDAY not in body)
check("SRT 含时间轴", srt.is_file() and "-->" in srt.read_text(encoding="utf-8"))
check("metadata 记录组名/日期/BV/UP",
      json.loads((group_dir / f"{DAY}.metadata.json").read_text(encoding="utf-8"))["bvid"] == EARLY)
check("无当日命中的组记为 SKIP 且不产出目录", "| SKIP | B组 |" in log_text()
      and not (RESULTS / "B组").exists())
check("所有 UP 取数失败的组记为 FAILED", "| FAILED | C组全失败 |" in log_text(), log_text())
check("单个 UP 失败只记 INFO、不影响该组产出",
      "拉投稿列表失败" in log_text() and f"| OK | {CFG['groups'][0]['name']} |" in log_text())

CALLS.clear()
run()
check("同日重跑被幂等跳过", CALLS == [] and "已处理过" in log_text(), CALLS)
check("重跑退出码仍为 1", run() == 1)

CALLS.clear()
run(["--force"])
check("--force 破除幂等并重跑", CALLS == [EARLY], CALLS)

CALLS.clear()
run(["--dry-run", "--force"])
check("--dry-run 不下载不转写", CALLS == [] and "| DRY |" in log_text(), CALLS)

CALLS.clear()
run(["--group", "B组", "--force"])
check("--group 只跑指定组", CALLS == [], CALLS)

CALLS.clear()
run(["--group", CFG["groups"][0]["name"], "--date", "2026-10-01", "--force"])
backfill = group_dir / "2026-10-01.txt"
check("--date 可补跑历史日期并单独成文件",
      CALLS == [YDAY] and backfill.is_file() and YDAY in backfill.read_text(encoding="utf-8"),
      (CALLS, listing(group_dir)))

print("RESULT:", "ALL PASS" if not FAILED_CHECKS else f"FAILED: {FAILED_CHECKS}")
sys.exit(1 if FAILED_CHECKS else 0)
