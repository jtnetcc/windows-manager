"""一条命令发版：递增版本 → 打包 → 推 GitHub → 传 OSS。

    python tools/release.py                 # 完整发版
    python tools/release.py --no-bump       # 重发当前版本
    python tools/release.py --skip-github   # 只打包 + 传 OSS
    python tools/release.py --skip-oss      # 只打包 + 推 GitHub
    python tools/release.py --dry-run       # 只看会做什么

需要的环境变量：

    GH_TOKEN     GitHub Personal Access Token（repo 权限）
    OSS_AK/OSS_SK  阿里云 AccessKey

各步骤都是调已有脚本，不重复实现：
- 打包   tools/build.py --bump（顺带归档到 releases/）
- 推送   tools/push_github.py（Git Data API，本机 git push 走不通）
- 分发   tools/sync_oss.py（上传 vX.Y.Z + latest）
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import version  # noqa: E402

REPO = "jtnetcc/windows-manager"
EXE = ROOT / "dist" / "Windows系统管家.exe"
PENDING = ROOT / "dist_pending"


def run(title: str, cmd: list[str], dry: bool = False) -> bool:
    print(f"\n{'=' * 50}\n▶ {title}\n{'=' * 50}")
    print("  " + " ".join(cmd))
    if dry:
        print("  （dry-run，跳过）")
        return True
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        print(f"\n✗ {title} 失败（退出码 {result.returncode}）")
        return False
    return True


def git_sync(message: str, dry: bool) -> bool:
    """把工作区变更纳入跟踪，否则 push 工具看不到新文件。"""
    if dry:
        print("\n  （dry-run，跳过 git add）")
        return True
    subprocess.run(["git", "add", "-A"], cwd=ROOT, check=False)
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True
    )
    if not status.stdout.strip():
        print("  工作区无变更，跳过本地提交")
        return True
    return run(
        "本地提交",
        ["git", "commit", "-q", "-m", message],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="一键发版")
    parser.add_argument("--no-bump", action="store_true", help="不递增版本")
    parser.add_argument("--skip-github", action="store_true")
    parser.add_argument("--skip-oss", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-m", "--message", default="", help="提交信息")
    args = parser.parse_args()

    do_github = not args.skip_github
    do_oss = not args.skip_oss

    # 前置检查：缺什么提前说，别跑到一半才失败
    missing = []
    if do_github and not (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")):
        missing.append("GH_TOKEN")
    if do_oss:
        for name in ("OSS_AK", "OSS_SK", "OSS_BUCKET", "OSS_ENDPOINT"):
            if not os.environ.get(name):
                missing.append(name)
    if missing:
        print("缺少环境变量：" + "、".join(missing))
        return 1

    target = version.bump_patch() if not args.no_bump else version.VERSION
    print(f"发版目标：v{version.VERSION} → v{target}" if not args.no_bump else f"重发：v{target}")

    # 1) 打包
    build_cmd = [sys.executable, "tools/build.py"]
    if not args.no_bump:
        build_cmd.append("--bump")
    if not run("打包", build_cmd, args.dry_run):
        return 1

    # 打包时如果旧版还在跑，exe 会落到 dist_pending，dist 里还是旧的。
    # 这时候继续发版会把旧包传上去，必须拦住。
    if not args.dry_run and PENDING.is_dir() and any(PENDING.iterdir()):
        print(
            "\n✗ dist_pending/ 里还有文件，说明打包时旧版本正在运行。\n"
            "  dist/ 里仍是旧 exe，继续发版会传错包。\n"
            "  请关闭所有「Windows系统管家」窗口后重跑。"
        )
        return 1

    if not args.dry_run and not EXE.is_file():
        print(f"✗ 没找到 {EXE}")
        return 1

    message = args.message or f"release: v{target}"

    # 2) 推 GitHub
    if do_github:
        if not git_sync(message, args.dry_run):
            return 1
        if not run(
            "推送 GitHub",
            [sys.executable, "tools/push_github.py", "--repo", REPO, "-m", message],
            args.dry_run,
        ):
            return 1

    # 3) 传 OSS
    if do_oss:
        if not run("同步 OSS", [sys.executable, "tools/sync_oss.py"], args.dry_run):
            return 1

    print(f"\n{'=' * 50}")
    print(f"✓ v{target} 发版完成")
    if do_github:
        print(f"  源码：https://github.com/{REPO}")
    if do_oss:
        endpoint = os.environ.get("OSS_ENDPOINT", "")
        if endpoint:
            print(f"  下载：https://{endpoint}/windows-manager/latest/Windows-Manager.exe")
    print(f"  回滚：python tools/releases.py --to {version.VERSION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
