"""单实例控制。

提权重启时，旧进程必须主动释放互斥量，否则新进程会被挡在门外，
用户看到的现象就是「点了提权，程序消失了但没重新打开」。
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

MUTEX_NAME = "Local\\MemoryKeeperSingleInstance"
ERROR_ALREADY_EXISTS = 183
ERROR_ACCESS_DENIED = 5

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = [
    ctypes.c_void_p,
    wintypes.BOOL,
    wintypes.LPCWSTR,
]
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL

_handle: int | None = None


def acquire(timeout: float = 0.0) -> bool:
    """尝试取得单实例锁；timeout 秒内会反复重试。

    提权重启时旧进程可能还没走完退出流程，给一点重试窗口能避免竞争失败。

    注意：**已有实例是提权进程时，CreateMutexW 会返回 NULL + ERROR_ACCESS_DENIED（5）**，
    而不是 ERROR_ALREADY_EXISTS。如果只判断 183，非提权进程会误以为「没人占」
    然后照常启动 —— 用户就会看到两个同名窗口。所以这里把「拿不到句柄」一律
    当作已被占用处理。
    """
    global _handle
    deadline = time.monotonic() + max(0.0, timeout)

    while True:
        handle = _kernel32.CreateMutexW(None, False, MUTEX_NAME)
        error = ctypes.get_last_error()

        # 只有「拿到句柄且没有 183」才算真的占住了
        if handle and error != ERROR_ALREADY_EXISTS:
            _handle = handle
            return True

        # 已存在时拿到的是别人的句柄，用完要关掉，否则会泄漏引用
        if handle:
            _kernel32.CloseHandle(handle)

        if time.monotonic() >= deadline:
            return False
        time.sleep(0.12)


def release() -> None:
    """主动释放锁，提权重启前调用。"""
    global _handle
    if _handle:
        _kernel32.CloseHandle(_handle)
        _handle = None


def activate_existing(prefix: str = "Windows系统管家") -> bool:
    """把已经在跑的窗口叫到前台。

    以前第二次启动是直接 `sys.exit(0)`，用户点了图标什么都没发生，
    看起来就像程序打不开。找到窗口就恢复并前置，找不到再安静退出。

    **不能用 FindWindowW**：它要求标题完全相等，而主窗口标题是
    「Windows系统管家 v0.0.11」—— 拿「Windows系统管家」去比永远不相等，
    这个函数会一直返回 False，等于没修。改成枚举顶层窗口按前缀匹配。
    """
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]

        hit: list[int] = []

        def _visit(hwnd, _lparam):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if buf.value.startswith(prefix):
                    hit.append(hwnd)
                    return False  # 找到就走，不用再枚举
            return True

        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(
            _visit
        )
        user32.EnumWindows(ctypes.cast(callback, ctypes.c_void_p), 0)
        if not hit:
            return False

        hwnd = wintypes.HWND(hit[0])
        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)
        # 前台权可能被系统拒（我们这个进程不是活动进程），失败也只是
        # 「窗口恢复了但没跳到最前」，比整个函数返回 False 好
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False
