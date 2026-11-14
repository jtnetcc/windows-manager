"""路径安全：任何删除操作之前都必须经过这里。

核心原则是 **fail-safe** —— 任何无法确认的情况一律按「受保护」处理。
宁可少清一个文件，也不能删错一个目录。

判定顺序（命中任意一条即保护）：
    1. 无法规范化（权限不足 / 不存在 / 路径畸形）  → 保护
    2. 磁盘根目录（C:\\）                          → 保护
    3. 系统关键文件精确匹配                        → 保护
    4. 系统目录前缀                                → 保护（可被显式放行）
    5. 用户主目录根                                → 保护
    6. 程序自身所在目录                            → 保护

系统级清理项（Windows.old、SoftwareDistribution\\Download 等）都在 `C:\\Windows`
之下，会被第 4 条拦住。放行方式是 `allow_prefixes`，但调用方必须同时满足
「用户显式勾选了这一项」和「候选路径确实落在该项声明的根目录内」两个条件，
避免出现「勾一次、全盘放行」。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from . import applog

# 受保护的目录前缀（小写比较）。系统盘 Windows 目录不写死在这里，
# 而是运行时按 WINDIR / SystemDrive 计算，兼容系统盘不是 C: 的机器。
PROTECTED_PREFIXES_WINDOWS = (
    r"c:\program files",
    r"c:\program files (x86)",
    r"c:\programdata",
    r"c:\$recycle.bin",
    r"c:\recovery",
)

# 受保护的单文件：删了系统起不来，或直接影响休眠/分页
PROTECTED_FILES_WINDOWS = (
    r"c:\pagefile.sys",
    r"c:\hiberfil.sys",
    r"c:\swapfile.sys",
    r"c:\bootmgr",
    r"c:\bootnxt",
)

# macOS 侧的前缀（提前留好，等 macos 后端接入时使用）
PROTECTED_PREFIXES_MACOS = (
    "/system",
    "/library",
    "/usr",
    "/bin",
    "/sbin",
    "/private",
    "/applications",
)


def is_windows() -> bool:
    return sys.platform == "win32"


def _system_windows_dirs() -> tuple[str, ...]:
    """系统盘上的 Windows 目录，动态取，不写死 C:。"""
    dirs: set[str] = set()
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if windir:
        dirs.add(windir.lower().replace("/", "\\").rstrip("\\"))
    drive = os.environ.get("SystemDrive")
    if drive:
        dirs.add(os.path.join(drive, "Windows").lower().replace("/", "\\"))
    return tuple(dirs)


def canonicalize(path: str | os.PathLike) -> str | None:
    """解析符号链接并转小写。失败返回 None（调用方应视为受保护）。

    必须用 strict=True：Python 的 realpath 默认对不存在的路径也会返回结果，
    那样「路径不存在」这种最该警惕的情况反而会被判成安全。strict 模式下
    解析失败会抛 OSError，正好落到 fail-safe 分支。
    """
    try:
        return os.path.realpath(os.fspath(path), strict=True).lower().replace("/", "\\")
    except (OSError, ValueError):
        return None


def _under(path: str, prefix: str) -> bool:
    """path 是否等于 prefix 或位于其下（按分隔符比较，避免前缀误匹配）。"""
    if not prefix:
        return False
    return path == prefix or path.startswith(prefix + "\\")


def is_protected(
    path: str | os.PathLike,
    allow_prefixes: tuple[str, ...] | list[str] | None = None,
) -> bool:
    """判断路径是否禁止删除。True = 不许动。"""
    canon = canonicalize(path)
    if canon is None:
        return True

    # 2. 磁盘根目录
    parent = os.path.dirname(canon)
    if parent in ("", "\\") or (len(parent) == 2 and parent.endswith(":")):
        return True
    if os.path.dirname(parent) == parent:  # 已经是根
        return True

    if not is_windows():
        return _check_macos(canon)

    # 3. 系统关键文件
    if canon in PROTECTED_FILES_WINDOWS:
        return True

    # 4. 系统目录前缀
    # 必须按「前缀 + 分隔符」比较，否则 `c:\program filesx` 会被当成
    # `c:\program files` 的子路径放过去。
    allow = tuple(
        str(a).lower().replace("/", "\\").rstrip("\\") for a in (allow_prefixes or ())
    )
    prefixes = PROTECTED_PREFIXES_WINDOWS + _system_windows_dirs()
    for prefix in prefixes:
        if not _under(canon, prefix):
            continue
        # 命中受保护前缀，但路径落在本次显式放行的目录内 → 放过
        if any(_under(canon, a) for a in allow):
            continue
        return True

    # 5. 用户主目录根
    home = canonicalize(Path.home())
    if home and canon == home:
        return True

    # 6. 程序自身所在目录
    try:
        exe_dir = canonicalize(Path(sys.executable).parent)
        if exe_dir and canon.startswith(exe_dir):
            return True
    except (OSError, ValueError):
        applog.silenced("safety.is_protected")

    return False


def _check_macos(canon: str) -> bool:
    posix = canon.replace("\\", "/")
    for prefix in PROTECTED_PREFIXES_MACOS:
        if posix == prefix or posix.startswith(prefix + "/"):
            return True
    home = os.path.realpath(str(Path.home())).lower()
    if posix == home:
        return True
    return False


def is_under_system_windows(path: str | os.PathLike) -> bool:
    """轻量判定：是否落在系统盘 Windows 目录下。

    扫描循环里高频调用，不做 realpath，删除保护仍由 is_protected 兜底。
    """
    if not is_windows():
        return False
    text = str(path).lower().replace("/", "\\")
    return any(text.startswith(w) for w in _system_windows_dirs())
