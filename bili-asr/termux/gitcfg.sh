#!/data/data/com.termux/files/usr/bin/bash
# Configure git for the GitHub account behind ~/setup/gh_token.txt, then prove the
# credential actually works against api.github.com without ever printing the token.
set -u
cd $HOME || exit 1
TOKEN=$(tr -d '\r\n' < ~/setup/gh_token.txt)

git config --global user.name "Ultrasteve"
git config --global user.email "13912587+Ultrasteve@users.noreply.github.com"
git config --global init.defaultBranch main
git config --global core.autocrlf input
git config --global core.fileMode false
git config --global credential.helper store
git config --global pull.rebase false

umask 077
printf 'https://Ultrasteve:%s@github.com\n' "$TOKEN" > $HOME/.git-credentials
chmod 600 $HOME/.git-credentials
echo "gitconfig: $(ls -l $HOME/.gitconfig | awk '{print $1}')"
echo "credentials: $(ls -l $HOME/.git-credentials | awk '{print $1}') size=$(wc -c < $HOME/.git-credentials)"

echo "--- identity ---"
git config --global --list | grep -aE "user\.|credential|init|core\."

echo "--- auth check (token never printed) ---"
python - <<'PY'
import subprocess, json, urllib.request
# Verify the stored credential file is what git will actually use, via a real git call.
out = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                     capture_output=True, text=True).stdout
got = dict(l.split("=", 1) for l in out.strip().splitlines() if "=" in l)
print("credential_fill:", {"protocol": got.get("protocol"), "host": got.get("host"),
                           "username": got.get("username"),
                           "password_len": len(got.get("password", ""))})
req = urllib.request.Request("https://api.github.com/user/repos?per_page=100&sort=updated",
                             headers={"Authorization": "Bearer " + got["password"], "User-Agent": "termux-setup"})
repos = json.loads(urllib.request.urlopen(req, timeout=30).read())
print("repo_count:", len(repos))
for r in repos[:10]:
    print("   ", r["full_name"], "| private" if r["private"] else "| public", "|", str(r.get("description") or "")[:40])
PY
echo "== gitconfig done =="
