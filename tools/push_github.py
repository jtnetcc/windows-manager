"""通过 GitHub REST API 推送当前工作区。

为什么不用 `git push`：本机网络只放行 `api.github.com`，`github.com` 被代理拦掉
（`CONNECT tunnel failed, response 502`），git 的 HTTPS 传输走不通。
所以改成调 Git Data API：blob → tree → commit → 移动分支。

用法：

    set GH_TOKEN=ghp_xxx
    python tools/push_github.py --repo jtnetcc/windows-manager -m "feat: xxx"
    python tools/push_github.py --repo jtnetcc/windows-manager --dry-run

Token 需要 `repo` 权限。脚本不会把 token 写进任何文件。

注意：
- 只推送 `git ls-files` 列出的文件（受 .gitignore 约束）
- 内容取自**暂存区**（`git cat-file blob :path`），不是磁盘：改完先 `git add` 再推，
  否则推上去的是上一次暂存的样子
- 默认建全新树，本地删掉的文件会一起从远端删掉；要保留用 `--keep-stale`
- 中文路径要关掉 `core.quotepath`，否则会变成 `\350\260\203` 这种转义
- 空仓库要先有一次提交，blob API 在空仓库上返回 409
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

API_ROOT = "https://api.github.com"
# 脚本可能在任意目录被调起，git 命令和文件读取都锚在项目根
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class GitHub:
    def __init__(self, repo: str, token: str):
        self.repo = repo
        self.token = token
        self.api = f"{API_ROOT}/repos/{repo}"

    def call(self, method: str, url: str, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        for key, value in (
            ("Authorization", f"Bearer {self.token}"),
            ("Accept", "application/vnd.github+json"),
            ("Content-Type", "application/json"),
            ("User-Agent", "windows-manager-push"),
        ):
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, json.load(resp)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")[:300]

    def head(self):
        """当前 main 的 (提交 sha, 树 sha)。空仓库返回 (None, None)。"""
        status, ref = self.call("GET", f"{self.api}/git/ref/heads/main")
        if status != 200:
            return None, None
        sha = ref["object"]["sha"]
        status, commit = self.call("GET", f"{self.api}/git/commits/{sha}")
        if status != 200:
            return None, None
        return sha, commit["tree"]["sha"]

    def paths(self, tree_sha: str) -> set[str]:
        """远端树里的全部文件路径，用来找「本地已删、远端还在」的残留。"""
        status, tree = self.call(
            "GET", f"{self.api}/git/trees/{tree_sha}?recursive=1"
        )
        if status != 200:
            return set()
        return {item["path"] for item in tree.get("tree", []) if item.get("type") == "blob"}


def local_files() -> list[str]:
    """工作区里被 git 跟踪的文件。关掉 quotepath 保住中文路径。"""
    out = subprocess.check_output(
        ["git", "-c", "core.quotepath=false", "ls-files"],
        text=True,
        cwd=ROOT,
    )
    return [line for line in out.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="用 GitHub API 推送工作区")
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("-m", "--message", default="", help="提交信息")
    parser.add_argument("--dry-run", action="store_true", help="只列文件，不真推")
    parser.add_argument("--jobs", type=int, default=6, help="并发上传数")
    parser.add_argument(
        "--keep-stale",
        action="store_true",
        help="保留远端有、本地已删除的文件（默认把它们一起删掉）",
    )
    args = parser.parse_args()

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("缺少 token：请先设置环境变量 GH_TOKEN")
        return 1

    files = local_files()
    if not files:
        print("工作区没有可推送的文件")
        return 1

    gh = GitHub(args.repo, token)
    head_sha, base_tree = gh.head()
    print(f"仓库 {args.repo}｜本地 {len(files)} 个文件")
    print(f"远端当前提交：{head_sha[:10] if head_sha else '（空仓库）'}")

    # 远端有、本地没有的文件。原来用 base_tree 增量更新，这类文件永远删不掉：
    # 本地删了 core/social.py，远端还挂着，看起来像「仓库里还有这份代码」。
    stale = sorted(gh.paths(base_tree) - set(files)) if base_tree else []
    if stale and not args.keep_stale:
        print(f"远端残留 {len(stale)} 个本地已删除的文件，本次一并清掉：")
        for name in stale[:10]:
            print("   -", name)
        if len(stale) > 10:
            print(f"   … 还有 {len(stale) - 10} 个")

    if args.dry_run:
        for name in files[:20]:
            print("  ", name)
        if len(files) > 20:
            print(f"   … 还有 {len(files) - 20} 个")
        print("dry-run，未上传")
        return 0

    def upload(path: str):
        # 内容取自 git 对象，不是磁盘。
        #
        # 开了 core.autocrlf 时工作区是 CRLF、仓库里存的是 LF，直接读磁盘会把
        # 每个文件都变成「整文件重写」：远端 tree 哈希和本地 HEAD 对不上，
        # GitHub 上的 diff 也会糊成一片。`git cat-file blob :path` 拿的是索引里
        # 那份字节，和本地提交完全一致。
        try:
            raw = subprocess.run(
                ["git", "-c", "core.quotepath=false", "cat-file", "blob", f":{path}"],
                cwd=ROOT,
                capture_output=True,
                check=True,
            ).stdout
        except subprocess.CalledProcessError:
            # 索引里没有（未跟踪）就退回磁盘，至少不会中断整个推送
            with open(os.path.join(ROOT, path), "rb") as handle:
                raw = handle.read()
        status, body = gh.call(
            "POST",
            f"{gh.api}/git/blobs",
            {"content": base64.b64encode(raw).decode(), "encoding": "base64"},
        )
        if status != 201:
            raise RuntimeError(f"{path} 上传失败：{status} {body}")
        return path, body["sha"]

    blobs = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for index, item in enumerate(pool.map(upload, files), 1):
            blobs.append(item)
            if index % 60 == 0:
                print(f"  已上传 {index}/{len(files)}")

    entries = [
        {"path": path, "mode": "100644", "type": "blob", "sha": sha}
        for path, sha in blobs
    ]
    payload = {"tree": entries}
    # 只在显式要求保留残留时挂 base_tree；默认建一棵全新的树，内容严格等于
    # `git ls-files`，删除才会真的同步过去。
    if base_tree and args.keep_stale:
        payload["base_tree"] = base_tree
    status, tree = gh.call("POST", f"{gh.api}/git/trees", payload)
    if status != 201:
        print("建树失败：", status, tree)
        return 1

    message = args.message or "chore: 同步工作区"
    commit_payload = {"message": message, "tree": tree["sha"]}
    if head_sha:
        commit_payload["parents"] = [head_sha]
    status, commit = gh.call("POST", f"{gh.api}/git/commits", commit_payload)
    if status != 201:
        print("建提交失败：", status, commit)
        return 1

    if head_sha:
        status, _ = gh.call(
            "PATCH", f"{gh.api}/git/refs/heads/main", {"sha": commit["sha"]}
        )
    else:
        status, _ = gh.call(
            "POST",
            f"{gh.api}/git/refs",
            {"ref": "refs/heads/main", "sha": commit["sha"]},
        )
    if status not in (200, 201):
        print("更新分支失败：", status)
        return 1

    print(f"已推送：{commit['sha'][:10]}  {message}")
    print(f"https://github.com/{args.repo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
