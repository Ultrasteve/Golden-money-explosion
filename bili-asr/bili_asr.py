#!/usr/bin/env python3
"""Windows/Python 3.11: Bilibili BV -> audio -> local SenseVoice -> TXT + SRT.

Install with pip (see accompanying instructions). The first ASR run downloads model
weights. SRT timing uses VAD boundaries when supplied; otherwise each 60-second
chunk receives one coarse cue. No guarantee of sentence/word-precise timestamps.
Only download videos that you are permitted to process.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import wave

BVID_RE = re.compile(r"BV[0-9A-Za-z]{10}\Z", re.IGNORECASE)
MEDIA_EXTS = {".m4a", ".mp3", ".mp4", ".webm", ".ogg", ".opus", ".flac", ".wav"}


def audit_tree(root: Path) -> None:
    """Print the actual directories after creation rather than an intended tree."""
    print("工作目录审计（实际目录）：", flush=True)
    for p in [root] + sorted((p for p in root.rglob("*") if p.is_dir()), key=str):
        depth = len(p.relative_to(root).parts)
        print("  " * depth + p.name + "/", flush=True)


def atomic_text(path: Path, text: str) -> None:
    pending = path.with_name(path.name + ".pending")
    pending.write_text(text, encoding="utf-8")
    os.replace(pending, path)


def timestamp(ms: int) -> str:
    ms = max(0, int(ms))
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def usable_downloads(folder: Path, bvid: str) -> list[Path]:
    return [p for p in folder.glob(bvid + ".*")
            if p.is_file() and p.suffix.lower() in MEDIA_EXTS and p.stat().st_size > 0]


def download_audio(
    bvid: str,
    media_dir: Path,
    insecure: bool,
    browser: str | None = None,
    cookies_file: Path | None = None,
    resume_download: bool = False,
) -> tuple[Path, str | None]:
    existing = usable_downloads(media_dir, bvid)
    if existing:
        print("复用已下载音频：", existing[0], flush=True)
        return existing[0], None
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise RuntimeError("缺少 yt-dlp，请先按安装说明使用 pip 安装") from exc

    url = f"https://www.bilibili.com/video/{bvid}"
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(media_dir / (bvid + ".%(ext)s")),
        "noplaylist": True,
        "socket_timeout": 25,
        "retries": 2,
        "fragment_retries": 2,
        "continuedl": resume_download,  # Default: restart partial downloads instead of Range resume.
        "nocheckcertificate": insecure,  # yt-dlp only; does not modify Python's global SSL settings.
    }
    from urllib.parse import quote

    
    if browser:
        # Equivalent to yt-dlp --cookies-from-browser; read only the current user's browser store.
        opts["cookiesfrombrowser"] = (browser, None, None, None)
        print(f"尝试读取本机 {browser} 浏览器 Cookie；仅用于你有访问权限的视频。", flush=True)
    if cookies_file:
        if not cookies_file.is_file():
            raise RuntimeError(f"Cookie 文件不存在：{cookies_file}")
        opts["cookiefile"] = str(cookies_file)
    if insecure:
        print("警告：已按要求跳过 yt-dlp HTTPS 证书验证；正式自动化前请修复证书链。", flush=True)
    print("下载策略：" + ("允许断点续传" if resume_download else "禁用断点续传，从头下载未完成文件"), flush=True)
    print("正在下载音频：", url, flush=True)
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:
        message = str(exc)
        if "HTTP Error 412" in message or "Precondition Failed" in message:
            raise RuntimeError(
                "B站返回 HTTP 412，拒绝了当前请求；这不是 SSL 故障。\n"
                "先确认同一网络下浏览器可以正常观看该视频，随后更新 yt-dlp。\n"
                "若确实需要自己的登录会话，可重试 --cookies-from-browser edge\n"
                "（或 --cookies-from-browser chrome / --cookies-file 文件）。\n"
                "Cookie 也可能无法解决 412；持续失败请停止自动重试、稍后再试，"
                "并使用 yt-dlp -v 采集不含凭据的诊断信息。\n"
                "不要将 Cookie、SESSDATA 或完整请求头公开。"
            ) from exc
        if "more expected" in message or "IncompleteRead" in message or "bytes read" in message:
            raise RuntimeError(
                "音频传输中途断开；本版已默认禁用断点续传。"
                "如依旧反复失败，请先检查代理/网络，稍后再试；不要无限重试。"
                "未完成的 .part 文件不会作为可用音频。"
            ) from exc
        raise
    downloads = usable_downloads(media_dir, bvid)
    if not downloads:
        raise RuntimeError("yt-dlp 未产生音频文件；查看其输出检查访问限制和网络故障")
    title = info.get("title") if isinstance(info, dict) else None
    return max(downloads, key=lambda p: p.stat().st_mtime), (title if title and title != "NA" else None)


def ffmpeg_exe() -> str:
    """imageio-ffmpeg ships no wheel for Android/bionic, so accept a system ffmpeg."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        found = shutil.which("ffmpeg")
    if not found:
        raise RuntimeError("未找到 ffmpeg：安装 imageio-ffmpeg，或让系统 ffmpeg 出现在 PATH 中")
    return found


def convert_wav(source: Path, wav_file: Path) -> None:
    if wav_file.exists() and wav_file.stat().st_size > 44:
        return
    pending = wav_file.with_name(wav_file.stem + ".pending.wav")
    pending.unlink(missing_ok=True)
    command = [ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
               "-c:a", "pcm_s16le", str(pending)]
    print("转换音频为 16kHz 单声道 WAV……", flush=True)
    try:
        subprocess.run(command, check=True)
        with wave.open(str(pending), "rb") as wav:
            if wav.getnframes() == 0 or wav.getframerate() != 16000 or wav.getnchannels() != 1:
                raise RuntimeError("转换后 WAV 参数异常或内容为空")
        os.replace(pending, wav_file)
    finally:
        pending.unlink(missing_ok=True)


def get_model(model_dir: str | None = None, vad_dir: str | None = None):
    try:
        from funasr import AutoModel
    except ImportError as exc:
        raise RuntimeError(f"FunASR 不可用（{exc}）；需安装 torch、torchaudio、funasr 及其依赖") from exc
    print("加载 SenseVoiceSmall + FSMN-VAD（首次使用需要联网下载模型）……", flush=True)
    # Bundled SenseVoice implementation; no arbitrary remote model.py execution.
    # Local dirs skip the modelscope/huggingface hub clients entirely.
    return AutoModel(
        model=model_dir or "iic/SenseVoiceSmall",
        vad_model=vad_dir or "fsmn-vad",
        vad_kwargs={"max_single_segment_time": 30000},
        trust_remote_code=False,
        device="cpu",
        disable_update=True,
    )


def clean_text(raw: object) -> str:
    from funasr.utils.postprocess_utils import rich_transcription_postprocess
    return rich_transcription_postprocess(str(raw or "")).strip()


def normalize_result(result: list, start_ms: int, duration_ms: int) -> dict:
    """Only use model timings when returned; fallback is explicitly chunk-level."""
    if not result or not isinstance(result[0], dict):
        raise RuntimeError("SenseVoice 没有返回预期的结果列表")
    item = result[0]
    text = clean_text(item.get("text", ""))
    entries = []
    for sent in item.get("sentence_info") or []:
        if not isinstance(sent, dict):
            continue
        try:
            left, right = int(sent["start"]), int(sent["end"])
        except (KeyError, TypeError, ValueError):
            continue
        segment_text = clean_text(sent.get("text") or sent.get("sentence") or "")
        if not segment_text or left < 0 or right <= left:
            continue
        left = min(left, duration_ms)
        right = min(right, duration_ms)
        if right > left:
            entries.append({"start_ms": start_ms + left,
                            "end_ms": start_ms + right, "text": segment_text})
    if entries:
        method = "vad_or_sentence_boundaries"
    elif text:
        entries = [{"start_ms": start_ms, "end_ms": start_ms + duration_ms, "text": text}]
        method = "coarse_chunk_boundary"
    else:
        method = "no_speech"
    return {"text": text, "entries": entries, "timing_method": method}


def transcribe_wav(wav_file: Path, chunks_dir: Path, chunk_seconds: int, language: str,
                   force: bool, model_dir: str | None = None,
                   vad_dir: str | None = None) -> list[dict]:
    with wave.open(str(wav_file), "rb") as src:
        rate = src.getframerate()
        if src.getnchannels() != 1 or src.getsampwidth() != 2 or rate != 16000:
            raise RuntimeError("输入必须为 16kHz、16bit、单声道 PCM WAV")
        frames = src.getnframes()
        total = math.ceil(frames / (rate * chunk_seconds))
        if total < 1:
            raise RuntimeError("音频为空")
        print(f"共 {frames / rate:.1f} 秒，{total} 段；每段最长 {chunk_seconds} 秒", flush=True)
        model = None
        output = []
        for idx in range(total):
            start_frame = idx * rate * chunk_seconds
            num_frames = min(rate * chunk_seconds, frames - start_frame)
            cached = chunks_dir / f"chunk-{idx:04d}.json"
            if cached.is_file() and not force:
                data = json.loads(cached.read_text(encoding="utf-8"))
                if data.get("start_frame") == start_frame and data.get("frames") == num_frames and data.get("language") == language:
                    output.append(data)
                    print(f"复用已完成片段 {idx + 1}/{total}", flush=True)
                    continue
            if model is None:
                model = get_model(model_dir, vad_dir)
            chunk_wav = chunks_dir / f"chunk-{idx:04d}.wav"
            src.setpos(start_frame)
            pcm = src.readframes(num_frames)
            with wave.open(str(chunk_wav), "wb") as dst:
                dst.setnchannels(1)
                dst.setsampwidth(2)
                dst.setframerate(rate)
                dst.writeframes(pcm)
            print(f"转录 {idx + 1}/{total}……", flush=True)
            try:
                result = model.generate(
                    input=str(chunk_wav), cache={}, language=language, use_itn=True,
                    batch_size_s=60, sentence_timestamp=True, merge_vad=False,
                )
                data = normalize_result(result, start_frame * 1000 // rate,
                                        num_frames * 1000 // rate)
                data.update({"start_frame": start_frame, "frames": num_frames, "language": language})
                atomic_text(cached, json.dumps(data, ensure_ascii=False, indent=2))
                output.append(data)
            finally:
                chunk_wav.unlink(missing_ok=True)
    return output


def format_srt(chunks: list[dict]) -> str:
    lines = []
    number = 0
    for chunk in chunks:
        for e in chunk["entries"]:
            number += 1
            lines.append(f"{number}\n{timestamp(e['start_ms'])} --> {timestamp(e['end_ms'])}\n{e['text']}")
    return "\n\n".join(lines) + ("\n" if lines else "")


def main() -> None:
    parser = argparse.ArgumentParser(description="BV号 → 下载音频 → SenseVoice本地转写 → TXT/SRT")
    parser.add_argument("bvid", help="例如 BV1sbeS6KESx")
    parser.add_argument("--output", type=Path, default=Path.cwd() / "bili-asr-windows",
                        help="单独工作根目录，不改动旧项目")
    parser.add_argument("--audio", type=Path, help="可选：本地音频测试，跳过网络下载")
    parser.add_argument("--language", choices=["auto", "zh", "en", "yue", "ja", "ko"], default="auto")
    parser.add_argument("--chunk-seconds", type=int, default=60)
    ssl_group = parser.add_mutually_exclusive_group()
    ssl_group.add_argument("--insecure", dest="insecure", action="store_true",
                           help="跳过 yt-dlp HTTPS 证书验证（此调试版默认启用）")
    ssl_group.add_argument("--verify-ssl", dest="insecure", action="store_false",
                           help="恢复 yt-dlp HTTPS 证书验证（修复证书后推荐使用）")
    parser.set_defaults(insecure=True)
    credential_group = parser.add_mutually_exclusive_group()
    credential_group.add_argument(
        "--cookies-from-browser", choices=["edge", "chrome", "firefox"],
        help="读取当前 Windows 浏览器的登录 Cookie；需要先在该浏览器中登录并关闭浏览器",
    )
    credential_group.add_argument(
        "--cookies-file", type=Path,
        help="使用你自己合法导出的 Netscape 格式 Cookie 文件；注意保密",
    )
    parser.add_argument("--download-only", action="store_true", help="仅测试音频下载，不加载 ASR")
    parser.add_argument("--resume-download", action="store_true", help="重新启用 yt-dlp 断点续传（默认关闭以修复异常续传）")
    parser.add_argument("--force", action="store_true", help="重新识别，忽略已完成结果与片段缓存")
    parser.add_argument("--model-dir", type=Path,
                        help="本地 SenseVoiceSmall 目录（含 model.pt/config.yaml），离线加载、不联网取权重")
    parser.add_argument("--vad-dir", type=Path,
                        help="本地 FSMN-VAD 目录，离线加载")
    args = parser.parse_args()
    bvid = args.bvid.strip()
    if not BVID_RE.fullmatch(bvid):
        parser.error("BV 号格式不正确；应为 BV 开头加 10 位字母数字")
    if not 15 <= args.chunk_seconds <= 300:
        parser.error("--chunk-seconds 必须在 15–300 之间")
    root = args.output.expanduser().resolve() / bvid
    media = root / "media"
    chunks = root / "chunks"
    results = root / "results"
    for directory in (media, chunks, results):
        directory.mkdir(parents=True, exist_ok=True)
    audit_tree(root)

    txt = results / (bvid + ".txt")
    srt = results / (bvid + ".srt")
    if not args.download_only and txt.exists() and srt.exists() and not args.force:
        print("已有结果，跳过：", txt, srt, sep="\n", flush=True)
        return

    if args.audio:
        audio = args.audio.expanduser().resolve()
        if not audio.is_file():
            raise RuntimeError(f"找不到本地音频：{audio}")
        title = None
    else:
        audio, title = download_audio(
            bvid, media, args.insecure, args.cookies_from_browser,
            args.cookies_file.expanduser().resolve() if args.cookies_file else None,
            args.resume_download,
        )
    if args.download_only:
        print("下载测试完成：", audio, flush=True)
        return
    wav_file = media / (bvid + "-16k.wav")
    # Keep the converted WAV under a distinctive name separate from the download.
    convert_wav(audio, wav_file)
    results_data = transcribe_wav(
        wav_file, chunks, args.chunk_seconds, args.language, args.force,
        str(args.model_dir.expanduser().resolve()) if args.model_dir else None,
        str(args.vad_dir.expanduser().resolve()) if args.vad_dir else None,
    )
    header = [f"BV：{bvid}", f"来源：https://www.bilibili.com/video/{bvid}"]
    if title:
        header.append(f"标题：{title}")
    header.append("时间索引为分块起点；SRT 优先使用模型返回的语音段边界，不保证逐词精度。")
    body = [f"[{timestamp(d['start_frame'] * 1000 // 16000).split(',')[0]}] {d['text']}"
            for d in results_data if d["text"]]
    atomic_text(txt, "\n".join(header + [""] + body) + "\n")
    atomic_text(srt, format_srt(results_data))
    atomic_text(results / (bvid + ".metadata.json"),
                json.dumps({"bvid": bvid, "title": title, "audio": str(audio),
                            "model": "iic/SenseVoiceSmall", "language": args.language,
                            "timing_methods": [d["timing_method"] for d in results_data]},
                           ensure_ascii=False, indent=2))
    print("\n完成：\nTXT：" + str(txt) + "\nSRT：" + str(srt), flush=True)
    if any(d["timing_method"] == "coarse_chunk_boundary" for d in results_data):
        print("提示：至少有一段未返回细粒度时间戳，其 SRT 为整段粗时间范围。", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
