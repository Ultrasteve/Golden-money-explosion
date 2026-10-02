#!/data/data/com.termux/files/usr/bin/bash
# Stage 3: per-package installs so one failing build cannot take the batch down.
# Pinned constraint: never let pip replace Termux's native (bionic) torch/llvmlite
# with a glibc wheel, which would import-fail.
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_BREAK_SYSTEM_PACKAGES=1
export PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
export PIP_EXTRA_INDEX_URL=https://pypi.org/simple/
PIN=$HOME/setup/pins.txt
cat > "$PIN" <<'EOF'
torch==2.11.0
torchaudio==2.11.0
numpy==2.4.4
scipy==1.18.0
llvmlite==0.47.0
EOF
export PIP_CONSTRAINT="$PIN"
PIPQ="pip install --no-input --no-cache-dir --retries 5 --timeout 60"

echo "== $(date) stage3 start =="
echo "--- pure python, deps allowed ---"
for p in pyyaml requests tqdm urllib3 idna certifi charset-normalizer six \
         filelock fsspec typing-extensions packaging platformdirs \
         kaldiio jieba jaconv msgpack; do
  if $PIPQ "$p" >/dev/null 2>&1; then echo "OK    $p"; else echo "FAIL  $p"; fi
done

echo "--- optional native builds, isolated, --no-deps ---"
for p in huggingface-hub editdistance soxr lazy-loader numba librosa torch_complex sentencepiece; do
  if $PIPQ --no-deps "$p" >/dev/null 2>&1; then echo "OK    $p"; else echo "FAIL  $p"; fi
done

echo "--- what pip sees ---"
pip list 2>/dev/null | tr -s ' '
echo "--- import audit ---"
python - <<'PY'
import importlib, traceback
mods = ["yaml","requests","tqdm","kaldiio","jieba","jaconv","editdistance","librosa",
        "numba","soxr","soundfile","torch_complex","huggingface_hub","funasr",
        "funasr.auto.auto_model","funasr.models.sense_voice.model",
        "funasr.frontends.frontend","funasr.utils.postprocess_utils"]
for m in mods:
    try:
        mod = importlib.import_module(m)
        print(f"OK    {m} {getattr(mod,'__version__','')}")
    except Exception as exc:
        print(f"FAIL  {m}: {type(exc).__name__}: {exc}")
import torch
print("torch still native:", torch.__file__)
print("funasr AutoModel present:", hasattr(__import__("funasr"), "AutoModel"))
PY
echo "== $(date) stage3 done =="
