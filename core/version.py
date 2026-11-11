"""版本号：唯一来源。

改版本只改这一个文件，界面标题、设置页、打包产物名都从这里读。
打包时加 `--bump` 会自动把 PATCH 位 +1 并写回本文件。
"""

from __future__ import annotations

VERSION = "0.0.27"
CHANNEL = "内测"


def text() -> str:
    """界面展示用，例如 v0.0.1·内测。"""
    return f"v{VERSION}·{CHANNEL}"


def parts() -> tuple[int, int, int]:
    major, minor, patch = (int(x) for x in VERSION.split("."))
    return major, minor, patch


def bump_patch() -> str:
    """PATCH +1，返回新版本号。0.0.1 → 0.0.2"""
    major, minor, patch = parts()
    return f"{major}.{minor}.{patch + 1}"
