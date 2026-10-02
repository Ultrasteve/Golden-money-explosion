#!/data/data/com.termux/files/usr/bin/bash
# SenseVoice's import chain dies on rapidfuzz (via funasr.metrics.common) and jamo (via
# build_tokenizer); a missing optional dep silently drops the model from the registry,
# which then surfaces as "model 'SenseVoiceSmall' is not registered".
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_BREAK_SYSTEM_PACKAGES=1
export PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
export PIP_EXTRA_INDEX_URL=https://pypi.org/simple/
export PIP_CONSTRAINT=$HOME/setup/pins.txt
export TMPDIR=$HOME/setup/tmp
export LDFLAGS="-llog"
cd $HOME/setup || exit 1

echo "== $(date) deps2 start =="
for p in jamo einops torch_complex; do
  if pip install --no-input --no-cache-dir --retries 3 --timeout 60 "$p" >/dev/null 2>&1; then echo "OK    $p"; else echo "FAIL  $p"; fi
done

echo "--- rapidfuzz build backend (meson-python) ---"
for p in meson meson-python pybind11; do
  if pip install --no-input --no-cache-dir --retries 3 --timeout 60 "$p" >/dev/null 2>&1; then echo "OK    $p"; else echo "FAIL  $p"; fi
done
which meson ninja cc++ clang g++ 2>&1 | sed 's/^/    /'

echo "--- rapidfuzz (C++ extension build) ---"
pip install --no-input --no-cache-dir --no-build-isolation rapidfuzz >rf_pip.txt 2>&1
echo "rf_rc=$?"
tail -6 rf_pip.txt
python -c "import rapidfuzz; print('RAPIDFUZZ_OK', rapidfuzz.__version__)" 2>&1 | tail -2

if pip install --no-input --no-cache-dir --retries 3 --timeout 60 jiwer >/dev/null 2>&1; then echo "OK    jiwer"; else echo "FAIL  jiwer"; fi

echo "--- sense_voice import chain ---"
FUNASR_IMPORT_DEBUG=1 python -c "import funasr.models.sense_voice.model; print('SENSEVOICE_MODULE_OK')" 2>&1 | grep -vE "torch_complex firstly" | tail -12

echo "--- AutoModel load ---"
timeout 900 python probe.py 2>&1 | grep -vE "torch_complex firstly|Failed to import" | tail -12
echo "== $(date) deps2 done =="
