#!/data/data/com.termux/files/usr/bin/bash
# Android/bionic needs liblog (__android_log_write) which sentencepiece never links, and the
# only targets that failed are the CLI tools the python extension does not need. Both are
# handled by patching build_bundled.sh in the unpacked sdist (already cmake_minimum-patched).
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_BREAK_SYSTEM_PACKAGES=1
export PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
export PIP_EXTRA_INDEX_URL=https://pypi.org/simple/
export PIP_CONSTRAINT=$HOME/setup/pins.txt
export TMPDIR=$HOME/setup/tmp
export LDFLAGS="-llog"
export CXXFLAGS="${CXXFLAGS} -llog"
cd $HOME/setup || exit 1

echo "== $(date) sp5 start =="
echo "--- original build_bundled.sh ---"
grep -n "cmake" sentencepiece-0.2.0/build_bundled.sh | head -5

sed -i 's/\bcmake \b/cmake -DSPM_BUILD_TOOL=OFF /g' sentencepiece-0.2.0/build_bundled.sh
echo "--- patched ---"
grep -n "cmake" sentencepiece-0.2.0/build_bundled.sh | head -5
rm -rf sentencepiece-0.2.0/build   # stale cache from the failed attempt

echo "--- compiling ---"
pip install --no-input --no-cache-dir --force-reinstall --no-deps --no-build-isolation \
  ./sentencepiece-0.2.0 >sp5_pip.txt 2>&1
echo "sp5_rc=$?"
grep -nE "error:|undefined symbol|Successfully installed" sp5_pip.txt | head -8
tail -4 sp5_pip.txt
python -c "import sentencepiece as spm; print('SPM_OK', spm.__version__)" 2>&1 | tail -3

echo "--- AutoModel load test ---"
timeout 900 python probe.py 2>&1 | grep -v 'torch_complex firstly' | tail -14
echo "== $(date) sp5 done =="
