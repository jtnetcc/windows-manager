"""Windows 路径与注册表根键的小工具。

这些函数原来在 core 里散着好几份：系统盘在 `metrics` 和 `slim` 各写一遍，
「文档」目录在 `appclean` 和 `social` 各写一遍，固定磁盘在 `apps` 和
`bigfile` 各写一遍（`apps` 那份没包 try/except，插着空读卡器时 GetDriveTypeW
会抛异常，整个扫描跟着挂）。改路径规则只该改一个地方。

判断「有没有装」一律看目录是否真的存在，这里的函数只负责给出候选路径，
不做存在性判断 —— 上层要用 `os.path.isdir` 自己确认。
"""

from __future__ import annotations

import ctypes
import os
import string
import winreg
from pathlib import Path

HKCU = "HKCU"
HKLM = "HKLM"

DRIVE_FIXED = 3


def system_drive() -> str:
    """系统盘根目录，带结尾反斜杠（`C:\\`）。"""
    drive = os.environ.get("SystemDrive") or "C:"
    return drive.rstrip("\\") + "\\"


def home() -> str:
    """当前用户目录。"""
    return str(Path.home())


def home_roots() -> list[str]:
    """扫描的默认起点：当前用户目录（用户数据都在这一棵树下）。"""
    return [home()]


def documents() -> str:
    """文档目录。重定向到 OneDrive 的用户这里取不到，调用方要判存在。"""
    return os.path.join(os.path.expanduser("~"), "Documents")


def fixed_drives() -> list[str]:
    """本地固定磁盘（跳过光驱、U 盘、网络盘，扫它们又慢又没意义）。"""
    drives: list[str] = []
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:
        return drives
    for index, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << index):
            continue
        root = f"{letter}:\\"
        try:
            if ctypes.windll.kernel32.GetDriveTypeW(root) == DRIVE_FIXED:
                drives.append(root)
        except Exception:
            # 单个盘符读不到就跳过，别让整次扫描陪葬
            continue
    return drives


def hive(name: str) -> int:
    """把 "HKCU" / "HKLM" 换成 winreg 的根键句柄。"""
    return winreg.HKEY_CURRENT_USER if name == HKCU else winreg.HKEY_LOCAL_MACHINE
