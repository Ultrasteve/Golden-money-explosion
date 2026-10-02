#!/data/data/com.termux/files/usr/bin/bash
# Speech-bearing smoke test: a sine tone only proves no crash, so feed espeak output
# through the real pipeline and require non-empty TXT/SRT.
cd ~/setup || exit 1
export TMPDIR=$HOME/setup/tmp
echo "== $(date) smoke2 start =="
if ! command -v espeak >/dev/null 2>&1; then
  apt-get install -y espeak >espeak_apt.log 2>&1 || pkg install -y espeak >>espeak_apt.log 2>&1
  tail -2 espeak_apt.log
fi
command -v espeak || { echo "NO_ESPEAK"; }

rm -rf ~/smoke ~/speech
mkdir -p ~/speech
espeak -s 130 -w ~/speech/raw.wav "The quick brown fox jumps over the lazy dog. This is a local speech recognition test running on an Android device." 2>&1 | tail -2
ls -l ~/speech/raw.wav
ffmpeg -y -hide_banner -loglevel error -i ~/speech/raw.wav -vn -ac 1 -ar 16000 ~/speech/speech16k.wav
echo "speech16k bytes=$(stat -c %s ~/speech/speech16k.wav)"

ffmpeg -y -hide_banner -loglevel error -f lavfi -i sine=frequency=440:duration=3 -ar 16000 -ac 1 ~/setup/tone.wav

python bili_asr.py BVtestsmoke1 --audio ~/setup/tone.wav --output ~/smoke \
  --model-dir ~/models/SenseVoiceSmall --vad-dir ~/models/fsmn-vad --chunk-seconds 15
echo "SINE_RC=$?"

python bili_asr.py BVtestsmoke2 --audio ~/speech/speech16k.wav --output ~/smoke \
  --model-dir ~/models/SenseVoiceSmall --vad-dir ~/models/fsmn-vad --chunk-seconds 15 --language en
echo "SPEECH_RC=$?"

echo "--- files ---"
find ~/smoke -type f -name "*.txt" -o -type f -name "*.srt" | sort
for f in ~/smoke/BVtestsmoke1/results/BVtestsmoke1.srt ~/smoke/BVtestsmoke2/results/BVtestsmoke2.txt; do
  echo "=== $f ==="; cat "$f" 2>&1; echo "(end)"
done
echo "== $(date) smoke2 done =="
