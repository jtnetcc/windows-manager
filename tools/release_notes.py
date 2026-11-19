"""生成 GitHub Release 说明（从交接文档的变更记录里摘）。

为什么要自动生成：发版说明最容易变成「复制上一版忘了改」。这里的唯一来源是
`docs/交接文档.md` 的「变更记录」表 —— 那一节是每次改动都要求同步维护的，
按版本号过滤比手写可靠。

用法：
    python tools/release_notes.py                 # 打到 stdout
    python tools/release_notes.py --out notes.md  # 写文件（CI 用）
    python tools/release_notes.py --version 0.0.63
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.version import CHANNEL, VERSION  # noqa: E402

HANDOVER = ROOT / "docs" / "交接文档.md"
ROW = re.compile(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(.+?)\s*\|\s*$")


def _clean(text: str) -> str:
    text = text.replace("**", "").replace("`", "").strip()
    # 变更记录里一行可能几百字，Release 说明保留前 180 字即可，
    # 细节让人回仓库看 —— 否则一屏全是同一段
    if len(text) > 180:
        text = text[:177] + "…"
    return text


def _current_sha() -> str:
    sha = os.environ.get("GITHUB_SHA", "")
    if sha:
        return sha[:7]
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=5,
        ).stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def collect(version: str) -> list[str]:
    """只保留提到该版本号的变更行，最多 12 条。"""
    if not HANDOVER.exists():
        return []
    wanted = f"v{version}"
    hits: list[str] = []
    for line in HANDOVER.read_text(encoding="utf-8").splitlines():
        match = ROW.match(line)
        if not match:
            continue
        if wanted not in line:
            continue
        hits.append(f"- {match.group(1)}：{_clean(match.group(2))}")
    return hits[-12:]


def build(version: str) -> str:
    lines = [
        f"## Windows系统管家 v{version}（{CHANNEL}）",
        "",
        f"- 提交：`{_current_sha()}`",
        f"- 渠道：{CHANNEL}（此版本标记为预发布，`latest` 不会指向它）",
        "",
        "### 本次变更",
    ]
    hits = collect(version)
    lines += hits or ["- 详见仓库 `docs/交接文档.md` 的变更记录。"]
    lines += [
        "",
        "### 下载与校验",
        "",
        "| 文件 | 说明 |",
        "|---|---|",
        f"| `Windows系统管家-v{version}.exe` | 便携版，单文件，双击即用 |",
        f"| `Windows系统管家-v{version}-setup.exe` | 安装包，按用户安装、可卸载 |",
        "| `SHA256SUMS.txt` | 上面两个文件的 SHA256 |",
        "",
        "校验（PowerShell）：",
        "",
        "```powershell",
        "Get-FileHash .\\Windows系统管家-v" + version + "-setup.exe -Algorithm SHA256",
        "# 与 SHA256SUMS.txt 里对应行比对",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Release 说明")
    parser.add_argument("--version", default=VERSION)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    text = build(args.version)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"已写入 {args.out}（{len(text)} 字节）")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
