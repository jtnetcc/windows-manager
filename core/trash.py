"""把文件送进回收站。

为什么不用 `os.remove`：删了就没了。清理工具最怕的就是「手滑一下找不回来」，
所以凡是用户数据（大文件、重复副本）一律走回收站，用户随时能还原。

底层用 `SHFileOperationW` + `FOF_ALLOWUNDO`，这是 Windows 自己的回收站机制，
和资源管理器里按 Delete 完全等价。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400

# 一次提交太多文件时，SHFileOperation 可能很慢甚至失败，分批送
BATCH = 200


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", wintypes.WORD),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def _shell32():
    lib = ctypes.WinDLL("shell32", use_last_error=True)
    lib.SHFileOperationW.argtypes = [ctypes.POINTER(_SHFILEOPSTRUCTW)]
    lib.SHFileOperationW.restype = ctypes.c_int
    return lib


def recycle(paths: list[str]) -> tuple[int, int]:
    """把文件送进回收站，返回 (成功数, 失败数)。

    `SHFileOperationW` 是按批次整体成功的，不会告诉你具体哪个失败。
    所以这里分批提交，失败批次再逐个重试一遍，才能给出准确的失败数。
    """
    paths = [p for p in paths if p]
    if not paths:
        return 0, 0

    ok_total = 0
    fail_total = 0
    for start in range(0, len(paths), BATCH):
        chunk = paths[start : start + BATCH]
        if _send(chunk):
            ok_total += len(chunk)
            continue
        # 整批失败：逐个再试一次，把坏苹果挑出来
        for path in chunk:
            if _send([path]):
                ok_total += 1
            else:
                fail_total += 1
    return ok_total, fail_total


def _send(paths: list[str]) -> bool:
    try:
        lib = _shell32()
        # pFrom 要求「双 null 结尾」的多字符串
        source = "\0".join(paths) + "\0\0"
        op = _SHFILEOPSTRUCTW()
        op.hwnd = None
        op.wFunc = FO_DELETE
        op.pFrom = source
        op.pTo = None
        op.fFlags = (
            FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
        )
        op.fAnyOperationsAborted = False
        result = lib.SHFileOperationW(ctypes.byref(op))
        return result == 0 and not op.fAnyOperationsAborted
    except Exception:
        return False


class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("i64Size", ctypes.c_longlong),
        ("i64NumItems", ctypes.c_longlong),
    ]


def stats() -> tuple[int, int]:
    """回收站当前占用：(字节数, 项目数)。

    SHQueryRecycleBinW 一次就能拿到，不用遍历 $Recycle.Bin —— 那个目录的
    命名规则随版本变化，遍历容易算错。
    """
    try:
        info = _SHQUERYRBINFO()
        info.cbSize = ctypes.sizeof(info)
        lib = _shell32()
        lib.SHQueryRecycleBinW.restype = ctypes.c_long
        if lib.SHQueryRecycleBinW(None, ctypes.byref(info)) == 0:
            return int(info.i64Size), int(info.i64NumItems)
    except Exception:
        pass
    return 0, 0


def total_size() -> int:
    return stats()[0]
