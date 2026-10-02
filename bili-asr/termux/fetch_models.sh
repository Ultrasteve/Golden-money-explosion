#!/data/data/com.termux/files/usr/bin/bash
# Fetch SenseVoiceSmall + FSMN-VAD weights straight from ModelScope into local dirs,
# so FunASR never needs the modelscope/huggingface clients (both pull unbuildable deps
# on bionic). -C - resumes, which matters: model.pt is 936 MB over a flaky phone link.
BASE=https://modelscope.cn/models
ROOT=$HOME/models

fetch() {  # fetch <repo-id> <target-dir> <file...>
  local id=$1 dir=$2 f
  mkdir -p "$dir"
  shift 2
  for f in "$@"; do
    printf '%-46s ' "$id/$f"
    if [ -s "$dir/$f" ]; then
      echo "cached"
      continue
    fi
    curl -fsSL --retry 12 --retry-delay 3 --retry-all-errors -C - \
         --connect-timeout 20 -o "$dir/$f" "$BASE/$id/resolve/master/$f" \
      && echo "OK" || { echo "FAILED"; rm -f "$dir/$f"; }
  done
}

echo "== $(date) models start =="
fetch iic/SenseVoiceSmall "$ROOT/SenseVoiceSmall" \
  model.pt config.yaml configuration.json am.mvn tokens.json \
  chn_jpn_yue_eng_ko_spectok.bpe.model

fetch iic/speech_fsmn_vad_zh-cn-16k-common-pytorch "$ROOT/fsmn-vad" \
  model.pt config.yaml configuration.json am.mvn

echo "--- expected sizes ---"
python - <<'PY'
import json, os, urllib.request
REPOS = (("iic/SenseVoiceSmall", "SenseVoiceSmall",
          ["model.pt", "config.yaml", "configuration.json", "am.mvn", "tokens.json",
           "chn_jpn_yue_eng_ko_spectok.bpe.model"]),
         ("iic/speech_fsmn_vad_zh-cn-16k-common-pytorch", "fsmn-vad",
          ["model.pt", "config.yaml", "configuration.json", "am.mvn"]))
bad = []
for mid, local, names in REPOS:
    url = f"https://modelscope.cn/api/v1/models/{mid}/repo/files?Recursive=true"
    sizes = {f["Path"]: f["Size"]
             for f in json.load(urllib.request.urlopen(url, timeout=30))["Data"]["Files"]
             if f["Type"] == "blob"}
    for n in names:
        path = os.path.join(os.path.expanduser("~/models"), local, n)
        have = os.path.getsize(path) if os.path.exists(path) else -1
        want = sizes.get(n)
        ok = want is not None and have == want
        if not ok:
            bad.append(f"{local}/{n}")
        print(f"{'OK ' if ok else 'BAD'} {local}/{n} have={have} want={want}")
print("ALL_PRESENT" if not bad else "INCOMPLETE: " + " ".join(bad))
PY
du -sh "$ROOT"/* 2>/dev/null
echo "== $(date) models done =="
