#!/usr/bin/env python3
"""bili_daily.py 的离线回归测试：伪造 yt-dlp 投稿列表/稿件信息与转录结果，不联网、不加载模型。

用法：python test_daily.py
"""

from __future__ import annotations

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


YDAY = "BV1YESTERDA0"       # 前一天 20:00，标题命中
LATE = "BV1LATE00001"       # 当天 09:30，标题命中（较晚）
TRAILER = "BV1TRAILER01"    # 当天 07:15，标题不命中
EARLY = "BV1EARLY0001"      # 当天 06:00，标题命中（应被选中）
OLD = "BV1OLDDAY001"        # 三天前
FLATMETA = "BV1FLATMTA01"  # 扁平条目自带标题+时间，不该再补查
NOISE = [f"BV1NOISE{i:04d}" for i in range(20)]  # 当天但标题全不命中，用来试补查上限

from bili_asr import BVID_RE  # noqa: E402

for _bvid in [YDAY, LATE, TRAILER, EARLY, OLD, FLATMETA] + NOISE:
    assert BVID_RE.fullmatch(_bvid), f"夹具用的 BV 号不合法: {_bvid}"

# 稿件标题/发布时间：真实扁平列表不给，靠 video_info 逐条补查
VIDEOS = {
    LATE: ("今日盘面复盘 0930", ts(9, 30)),
    TRAILER: ("今晚直播预告", ts(7, 15)),
    YDAY: ("昨日复盘", ts(20, 0, -1)),
    EARLY: ("早盘复盘 0600", ts(6, 0)),
    OLD: ("复盘（旧）", ts(9, 0, -3)),
    FLATMETA: ("隔夜复盘速览", ts(1, 30)),
}
for _i, _b in enumerate(NOISE):
    VIDEOS[_b] = (f"无关内容 {_i}", ts(8, _i % 60))

LISTS = {
    "111": [{"id": LATE}, {"id": TRAILER}, {"id": YDAY}],
    "222": [{"url": "https://www.bilibili.com/video/" + EARLY + "?spm_id_from=333.788.videopod.sections"}],
    "333": "RAISE",
    "444": [{"id": OLD}, {"id": LATE}],
    "555": [{"id": b} for b in NOISE],
    "666": [{"id": FLATMETA, "title": "隔夜复盘速览", "timestamp": ts(1, 30)}],
}

PROBED: list[str] = []


class FakeYoutubeDL:
    """替身：space URL 返回扁平投稿列表，video URL 返回稿件标题/时间。"""

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        assert download is False, "选片阶段不应下载"
        if "space.bilibili.com" in url:
            assert self.opts.get("extract_flat") == "in_playlist"
            data = LISTS[url.split("/")[-2]]
            if data == "RAISE":
                raise RuntimeError("HTTP Error 412: Precondition Failed")
            limit = int(self.opts["playlist_items"].split("-")[1])
            return {"entries": [dict(entry) for entry in data[:limit]]}
        assert self.opts.get("extract_flat") is None, "补查稿件信息不该再用扁平模式"
        bvid = url.rsplit("/", 1)[-1].split("?", 1)[0]
        title, stamp = VIDEOS[bvid]
        PROBED.append(bvid)
        return {"id": bvid, "title": title, "timestamp": stamp}


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
    "chunk_seconds": 60, "recent_limit": 5, "probe_limit": 12,
    "groups": [
        {"name": "A组/异常:字符", "title_pattern": "复盘", "ups": [
            {"uid": "111", "alias": "UP甲"}, {"uid": "222", "alias": "UP乙"},
            {"uid": "333", "alias": "UP丙"}]},
        {"name": "B组", "title_pattern": "复盘", "ups": [{"uid": "444", "alias": "UP丁"}]},
        {"name": "C组全失败", "title_pattern": ".", "ups": [{"uid": "333", "alias": "UP丙"}]},
        {"name": "D组探查上限", "title_pattern": "复盘", "recent_limit": 20,
         "probe_limit": 3, "ups": [{"uid": "555", "alias": "UP戊"}]},
        {"name": "E组扁平自带时间", "title_pattern": "复盘",
         "ups": [{"uid": "666", "alias": "UP己"}]},
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
check("扁平列表只有 BV 号时靠补查拿到标题与时间", LATE in PROBED and EARLY in PROBED, PROBED)
check("补查遇到更早稿件即停（UP乙 一条就够）", PROBED.count(EARLY) == 1, PROBED)
check("扁平条目自带标题+时间时不再补查",
      FLATMETA not in PROBED and f"| OK | {CFG['groups'][4]['name']} |" in log_text(),
      (PROBED, log_text()))
check("无当日命中的组记为 SKIP 且不产出目录", "| SKIP | B组 |" in log_text()
      and not (RESULTS / "B组").exists())
check("所有 UP 取数失败的组记为 FAILED", "| FAILED | C组全失败 |" in log_text(), log_text())
check("单个 UP 失败只记 INFO、不影响该组产出",
      "拉投稿列表失败" in log_text() and f"| OK | {CFG['groups'][0]['name']} |" in log_text())
check("补查达到上限时记 INFO 并 SKIP",
      "补查达到上限 3 条" in log_text() and "| SKIP | D组探查上限 |" in log_text(), log_text())

CALLS.clear()
PROBED.clear()
run()
check("同日重跑被幂等跳过", CALLS == [] and "已处理过" in log_text(), CALLS)
check("幂等跳过时不再补查已完成组的稿件",
      not (set(PROBED) & {LATE, TRAILER, YDAY, EARLY, FLATMETA}), PROBED)
check("重跑退出码仍为 1", run() == 1)

CALLS.clear()
run(["--force"])
check("--force 破除幂等并重跑", CALLS == [EARLY, FLATMETA], CALLS)

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
