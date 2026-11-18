"""空间项的统一测量源。

原先同一块空间有两套算法：`slim`（动作模块）自己算休眠文件/转储/旧系统文件，
`spacereport`（测量模块）又算一遍，还为了避开循环导入反过来从 `slim` 里
`import _dump_size`。两边口径一旦有人只改一处，界面上就会出现两个数字。

现在收拢到这里：
- `slim` 只负责「怎么关 / 怎么删」，体积一律问这里；
- `spacereport` 只负责「列清单 / 给按钮」，体积也问这里。

所有函数都是「拿不到就返回 0」，不抛异常 —— 这些值用于展示，不该让整页失败。
"""

from __future__ import annotations

import os

from .winpath import system_drive

# Windows.old / WinSxS 这类目录动辄十几万文件，全量遍历要好几秒，
# 而用户只想知道「大概多大」。给个上限，够判断量级。
WALK_LIMIT = 60000


def file_size(*names: str) -> int:
    """系统盘根目录下若干文件的体积之和（休眠、分页、交换文件都在这里）。"""
    total = 0
    for name in names:
        try:
            total += os.path.getsize(os.path.join(system_drive(), name))
        except OSError:
            continue
    return total


def dir_size(path: str, limit: int = WALK_LIMIT) -> int:
    """目录体积，带文件数上限。"""
    if not os.path.isdir(path):
        return 0
    total = 0
    seen = 0
    for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
            seen += 1
            if seen >= limit:
                return total
    return total


# ------------------------------------------------------------------ 各空间项


def hiberfil_path() -> str:
    return os.path.join(system_drive(), "hiberfil.sys")


def hibernation_size() -> int:
    try:
        return os.path.getsize(hiberfil_path())
    except OSError:
        return 0


def pagefile_size() -> int:
    return file_size("pagefile.sys", "swapfile.sys")


def dump_files() -> list[str]:
    """内存转储：完整转储 + 小转储目录 + 内核实时转储。"""
    windir = os.environ.get("WINDIR", os.path.join(system_drive(), "Windows"))
    paths = [os.path.join(windir, "MEMORY.DMP")]
    for sub in ("Minidump", "LiveKernelReports"):
        folder = os.path.join(windir, sub)
        if not os.path.isdir(folder):
            continue
        for root, _dirs, files in os.walk(folder, onerror=lambda _e: None):
            paths += [os.path.join(root, name) for name in files]
    return [path for path in paths if os.path.isfile(path)]


def dump_size() -> int:
    total = 0
    for path in dump_files():
        try:
            total += os.path.getsize(path)
        except OSError:
            continue
    return total


def windows_old_path() -> str:
    return os.path.join(system_drive(), "Windows.old")


def windows_old_size() -> int:
    return dir_size(windows_old_path())
