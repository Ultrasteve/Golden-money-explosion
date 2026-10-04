#!/usr/bin/env python3
"""把 results/ 下转写好的 txt 通过 api.github.com（Git Data API）推到 GitHub 仓库。

手机上 github.com 不通，git push/clone 都没法用，但 api.github.com 通，所以全程走 REST：
GET ref -> POST blobs -> POST tree(base_tree) -> POST commit -> PATCH ref（非快进自动重试）。
只推 <组名>/<日期>.txt 到 <prefix>/<组名>/<日期>.txt；published.json 台账记每个文件的
sha256，内容没变就不再推；--dry-run 只列计划。只用 Python 标准库，不用 git、不用第三方包。

示例：python bili_publish.py --config groups.json --token-file ~/gh_token.txt --dry-run
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import ssl
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"


def api(method: str, path: str, token: str, payload: dict | None = None,
        insecure: bool = True) -> tuple[int, dict]:
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json",
                 "User-Agent": "bili-asr-publish"})
    context = ssl._create_unverified_context() if insecure else None
    try:
        with urllib.request.urlopen(req, timeout=60, context=context) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:400]
        try:
            return exc.code, json.loads(body)
        except Exception:
            return exc.code, {"message": body}


def read_token(path: Path) -> str:
    if not path.is_file():
        raise RuntimeError(f"找不到 token 文件：{path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            return line.strip()
    raise RuntimeError(f"token 文件是空的：{path}")


def local_plan(results: Path, prefix: str, groups: set | None) -> list[tuple[Path, str]]:
    """返回 [(本地 txt, 仓库内路径)]，只收 results/<组名>/ 下的 .txt。"""
    plan: list[tuple[Path, str]] = []
    if not results.is_dir():
        return plan
    for group_dir in sorted(results.iterdir()):
        if not group_dir.is_dir() or (groups and group_dir.name not in groups):
            continue
        for f in sorted(group_dir.glob("*.txt")):
            plan.append((f, f"{prefix}/{group_dir.name}/{f.name}"))
    return plan


class Published:
    """published.json 台账：内容 sha256 与已推送一致的文件直接跳过。"""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.data: dict[str, dict] = {}
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                loaded = None
            if isinstance(loaded, dict) and isinstance(loaded.get("files"), dict):
                self.data = {k: v for k, v in loaded["files"].items() if isinstance(v, dict)}

    def changed(self, dest: str, digest: str) -> bool:
        record = self.data.get(dest)
        return not (record and record.get("sha256") == digest)

    def mark(self, dest: str, digest: str, size: int, commit: str) -> None:
        self.data[dest] = {"sha256": digest, "bytes": size, "commit": commit,
                           "pushed_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        tmp = self.path.with_name(self.path.name + ".pending")
        tmp.write_text(json.dumps({"files": self.data}, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self.path)


def commit_files(items: list[tuple[str, bytes]], repo: str, branch: str,
                 token: str, message: str, insecure: bool) -> str:
    """一个提交带上全部待推文件并推进分支；非快进（有人同时写分支）时重取 ref 重试。"""
    last_error = ""
    for _attempt in range(3):
        status, ref = api("GET", f"/repos/{repo}/git/ref/heads/{branch}", token,
                          insecure=insecure)
        if status != 200:
            raise RuntimeError(f"取分支 {branch} 失败（HTTP {status}）：{str(ref)[:200]}")
        base = ref["object"]["sha"]
        tree = []
        for dest, data in items:
            status, blob = api("POST", f"/repos/{repo}/git/blobs", token,
                               {"content": base64.b64encode(data).decode(),
                                "encoding": "base64"}, insecure=insecure)
            if status not in (200, 201):
                raise RuntimeError(f"建 blob {dest} 失败（HTTP {status}）：{str(blob)[:200]}")
            tree.append({"path": dest, "mode": "100644", "type": "blob",
                         "sha": blob["sha"]})
        status, made = api("POST", f"/repos/{repo}/git/trees", token,
                           {"base_tree": base, "tree": tree}, insecure=insecure)
        if status not in (200, 201):
            raise RuntimeError(f"建 tree 失败（HTTP {status}）：{str(made)[:200]}")
        status, commit = api("POST", f"/repos/{repo}/git/commits", token,
                             {"message": message, "tree": made["sha"],
                              "parents": [base]}, insecure=insecure)
        if status not in (200, 201):
            raise RuntimeError(f"建 commit 失败（HTTP {status}）：{str(commit)[:200]}")
        status, done = api("PATCH", f"/repos/{repo}/git/refs/heads/{branch}", token,
                           {"sha": commit["sha"], "force": False}, insecure=insecure)
        if status == 200:
            return str(commit["sha"])
        last_error = f"HTTP {status}: {str(done)[:200]}"
    raise RuntimeError(f"推分支 {branch} 重试 3 次仍失败（可能有别的进程在同时写）：{last_error}")


def main() -> None:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="只用 api.github.com，把转写 txt 单向上推到仓库的指定路径（不用 git、不做同步）")
    parser.add_argument("--config", type=Path, default=here / "groups.json",
                        help="复用 bili_daily 的配置，只读 output_root / publish_* 字段")
    parser.add_argument("--output", type=Path, help="覆盖配置里的 output_root")
    parser.add_argument("--repo", help="owner/name，默认取配置 publish_repo")
    parser.add_argument("--branch", default=None, help="默认取配置 publish_branch，再默认 main")
    parser.add_argument("--prefix", default=None, help="仓库内目录，最终路径 <prefix>/<组名>/<日期>.txt，默认 daily")
    parser.add_argument("--token-file", type=Path,
                        help="能写该仓库的 PAT（每行取第一个非空行），默认配置 publish_token_file 或 ~/gh_token.txt")
    parser.add_argument("--group", action="append", help="只推指定组名，可重复")
    parser.add_argument("--dry-run", action="store_true", help="只列将要推的文件，不调 API")
    parser.add_argument("--secure", action="store_true",
                        help="校验 TLS 证书（默认不校验：Termux 常缺系统 CA，且要求可直连 api.github.com）")
    args = parser.parse_args()

    cfg: dict = {}
    cfg_path = args.config.expanduser()
    if cfg_path.is_file():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    root = (args.output or Path(cfg.get("output_root") or (here / "bili-daily"))).expanduser().resolve()
    repo = args.repo or cfg.get("publish_repo")
    if not repo or "/" not in str(repo):
        raise SystemExit("必须用 --repo 或配置 publish_repo 指定 owner/name")
    branch = args.branch or cfg.get("publish_branch") or "main"
    prefix = str(args.prefix or cfg.get("publish_prefix") or "daily").strip("/")
    token_file = Path(args.token_file or cfg.get("publish_token_file")
                      or "~/gh_token.txt").expanduser()

    wanted = set(args.group) if args.group else None
    published = Published(root / "published.json")
    todo: list[tuple[str, bytes]] = []
    for f, dest in local_plan(root / "results", prefix, wanted):
        data = f.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if not published.changed(dest, digest):
            print(f"SKIP  {dest}（与已推送内容一致）")
            continue
        print(f"PUSH  {dest}  <- {f}（{len(data)} 字节）")
        todo.append((dest, data))
    if not todo:
        print("没有需要推送的文件。")
        return
    if args.dry_run:
        print(f"--dry-run：共 {len(todo)} 个文件待推，未调用 API。")
        return
    token = read_token(token_file)
    names = ", ".join(dest.rsplit("/", 1)[-1] for dest, _ in todo[:6])
    message = f"publish: {names}" + ("" if len(todo) <= 6 else f" 等 {len(todo)} 个文件")
    sha = commit_files(todo, str(repo), str(branch), token, message, insecure=not args.secure)
    for dest, data in todo:
        published.mark(dest, hashlib.sha256(data).hexdigest(), len(data), sha)
    print(f"已推送提交 {sha[:10]} 到 {repo}@{branch}（{len(todo)} 个文件）")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        print(f"失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
