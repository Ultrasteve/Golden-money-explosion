#!/data/data/com.termux/files/usr/bin/bash
# Stage 1: Termux repo packages needed by the BV -> ASR pipeline.
export DEBIAN_FRONTEND=noninteractive
export APT_LISTS_ONLY=0

echo "== $(date) start =="
apt-get install -y \
  python python-pip python-ensurepip-wheels \
  python-numpy python-scipy python-llvmlite \
  python-torch python-torchaudio python-yt-dlp \
  ffmpeg libsndfile curl git \
  2>&1
echo "APT_RC=$?"

echo "== versions =="
python -V
pip -V | head -1
for c in ffmpeg ffprobe curl git; do printf "%-8s " "$c"; command -v "$c" || echo MISSING; done
ffmpeg -version 2>/dev/null | head -1
python - <<'PY'
mods = ("numpy", "scipy", "torch", "torchaudio", "llvmlite", "yt_dlp")
for m in mods:
    try:
        mod = __import__(m)
        print(f"{m:12s} OK  {getattr(mod, '__version__', '?')}")
    except Exception as exc:
        print(f"{m:12s} FAIL {type(exc).__name__}: {exc}")
PY
echo "== $(date) stage1 done =="
