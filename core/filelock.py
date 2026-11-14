"""文件被谁占着：查占用者，并在用户同意后请它退出。

清理完还剩几个删不掉时，界面原来只会给一个「重试剩余 N 个」——用户不知道
是谁占着，点多少次结果都一样。Windows 上查占用最稳的是 Restart Manager
（`RmStartSession` / `RmRegisterResources` / `RmGetList`）：安装程序、更新器
都用它，不需要驱动、不用遍历全系统句柄，返回的就是「正在使用这批文件的进程」。

关闭分两步：先给持有进程发 WM_CLOSE 让它正常退出（浏览器会正常收尾、落盘），
过一会儿还活着的再按进程树强杀。浏览器的缓存往往由渲染进程持有，只关渲染
进程的话主进程立刻又写回来 —— 所以先顺着同名 exe 的父链找到应用主进程，
整棵树一起关。

系统关键进程（explorer、svchost、安全软件等）一律不关：关掉 explorer 会
连带任务栏一起消失，收益却不成正比，界面上只做提示。
"""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from functools import lru_cache

import psutil

from . import apps, safety
from .optimizer import CRITICAL_PROCESSES, terminate_tree
from . import applog

# Restart Manager 只需要「谁在占用」，传太多文件会拖慢查询
MAX_FILES = 400

ERROR_MORE_DATA = 234
CCH_RM_SESSION_KEY = 32
CCH_RM_MAX_APP_NAME = 255
CCH_RM_MAX_SVC_NAME = 63

WM_CLOSE = 0x0010


class _RM_UNIQUE_PROCESS(ctypes.Structure):
    _fields_ = [
        ("dwProcessId", wintypes.DWORD),
        ("ProcessStartTime", wintypes.FILETIME),
    ]


class _RM_PROCESS_INFO(ctypes.Structure):
    _fields_ = [
        ("Process", _RM_UNIQUE_PROCESS),
        ("strAppName", wintypes.WCHAR * (CCH_RM_MAX_APP_NAME + 1)),
        ("strServiceShortName", wintypes.WCHAR * (CCH_RM_MAX_SVC_NAME + 1)),
        ("ApplicationType", ctypes.c_int),
        ("AppStatus", wintypes.ULONG),
        ("TSSessionId", wintypes.DWORD),
        ("bRestartable", wintypes.BOOL),
    ]


@lru_cache(maxsize=1)
def _rm() -> "ctypes.WinDLL | None":
    """rstrtmgr.dll 的句柄和函数签名。查不到就返回 None，调用方退化成「查不到占用」。"""
    if not safety.is_windows():
        return None
    try:
        dll = ctypes.WinDLL("rstrtmgr", use_last_error=True)
    except OSError:
        return None

    dll.RmStartSession.argtypes = [
        ctypes.POINTER(wintypes.DWORD),
        wintypes.DWORD,
        wintypes.LPWSTR,
    ]
    dll.RmStartSession.restype = wintypes.DWORD
    dll.RmRegisterResources.argtypes = [
        wintypes.DWORD,
        wintypes.UINT,
        ctypes.POINTER(wintypes.LPCWSTR),
        wintypes.UINT,
        ctypes.c_void_p,
        wintypes.UINT,
        ctypes.c_void_p,
    ]
    dll.RmRegisterResources.restype = wintypes.DWORD
    dll.RmGetList.argtypes = [
        wintypes.DWORD,
        ctypes.POINTER(wintypes.UINT),
        ctypes.POINTER(wintypes.UINT),
        ctypes.POINTER(_RM_PROCESS_INFO),
        ctypes.POINTER(wintypes.DWORD),
    ]
    dll.RmGetList.restype = wintypes.DWORD
    dll.RmEndSession.argtypes = [wintypes.DWORD]
    dll.RmEndSession.restype = wintypes.DWORD
    return dll


def _locker_pids(paths: list[str]) -> list[int]:
    """Restart Manager 查这批文件的持有进程。任何一步失败都返回空表。

    查询是「尽力而为」：拿不到结果时界面退回原来的「重试」，不做任何猜测。
    """
    dll = _rm()
    if dll is None or not paths:
        return []

    session = wintypes.DWORD(0)
    key = ctypes.create_unicode_buffer(CCH_RM_SESSION_KEY + 1)
    if dll.RmStartSession(ctypes.byref(session), 0, key) != 0:
        return []

    try:
        array = (wintypes.LPCWSTR * len(paths))(*paths)
        if (
            dll.RmRegisterResources(session, len(paths), array, 0, None, 0, None)
            != 0
        ):
            return []

        needed = wintypes.UINT(0)
        count = wintypes.UINT(0)
        reasons = wintypes.DWORD(0)
        # 缓冲区先传 None：返回 ERROR_MORE_DATA 时 needed 才是真实条数
        if (
            dll.RmGetList(
                session, ctypes.byref(needed), ctypes.byref(count), None,
                ctypes.byref(reasons),
            )
            != ERROR_MORE_DATA
            or not needed.value
        ):
            return []

        infos = (_RM_PROCESS_INFO * needed.value)()
        count = wintypes.UINT(needed.value)
        if (
            dll.RmGetList(
                session, ctypes.byref(needed), ctypes.byref(count), infos,
                ctypes.byref(reasons),
            )
            != 0
        ):
            return []
        return [int(infos[i].Process.dwProcessId) for i in range(count.value)]
    finally:
        dll.RmEndSession(session)


def _app_root(proc: psutil.Process, me: int) -> psutil.Process:
    """同名 exe 的祖先链顶端：应用主进程。

    浏览器的缓存由渲染进程持有，只关渲染进程的话主进程马上又写回来；
    顺着 chrome.exe 的父链往上走，顶层那个才是「Chrome」。

    走到本程序自己就停：从别的同名程序里拉起来的进程，不能算成本程序的一部分
    （否则会被「不关自己」的规则连带保护起来，永远关不掉）。
    """
    root = proc
    try:
        name = (proc.name() or "").lower()
    except (psutil.Error, OSError):
        return root

    for _ in range(8):
        try:
            parent = root.parent()
            if (
                parent is None
                or parent.pid == me
                or (parent.name() or "").lower() != name
            ):
                break
            root = parent
        except (psutil.Error, OSError):
            break
    return root


@dataclass
class Holder:
    """一个占着文件的程序。

    pid 是应用主进程（关它 = 关整个程序），pids 是整棵树的进程号；
    closable=False 表示系统关键进程，界面只提示、不去关。
    """

    pid: int
    name: str
    pids: list[int] = field(default_factory=list)
    closable: bool = True


def lockers(paths: list[str]) -> list[Holder]:
    """查出占用这批文件的程序，按应用归组；可关的排前面。"""
    pids = _locker_pids([p for p in paths if p][:MAX_FILES])
    if not pids:
        return []

    me = os.getpid()
    grouped: dict[int, Holder] = {}
    for pid in pids:
        if pid in (0, 4):
            continue
        try:
            proc = psutil.Process(pid)
            root = _app_root(proc, me)
            exe = root.exe() or ""
        except (psutil.Error, OSError):
            continue

        pid_root = root.pid
        holder = grouped.get(pid_root)
        if holder is None:
            raw = os.path.basename(exe) if exe else (proc.name() or f"PID {pid}")
            critical = (
                pid_root == me
                or raw.lower() in CRITICAL_PROCESSES
                or (proc.name() or "").lower() in CRITICAL_PROCESSES
            )
            holder = Holder(
                pid=pid_root,
                name=_title(raw, exe),
                closable=not critical,
            )
            grouped[pid_root] = holder
        holder.pids.append(pid)

    return sorted(grouped.values(), key=lambda h: (not h.closable, h.name.lower()))


def _title(raw: str, exe: str) -> str:
    """显示名：优先 exe 版本资源里的 ProductName。

    「关闭微信」比「关闭 WeChat.exe」清楚；拿不到版本资源就退回文件名（去后缀）。
    """
    if exe:
        title = apps.exe_title(exe)
        if title:
            return title
    return os.path.splitext(raw)[0]


def _post_close(pid: int) -> None:
    """给该进程的顶层窗口发 WM_CLOSE，等于点窗口的「关闭」。"""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    wnd_enum = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    handles: list[int] = []

    def callback(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            handles.append(hwnd)
        return True

    try:
        user32.EnumWindows(wnd_enum(callback), 0)
    except Exception:
        return
    for hwnd in handles:
        try:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        except Exception:
            applog.silenced("filelock._post_close")


def close(
    holders: list[Holder],
    should_stop=lambda: False,
    grace: float = 2.5,
) -> tuple[int, int]:
    """请这些程序退出，返回 (关掉的程序数, 没关掉的程序数)。

    先 WM_CLOSE 走正常退出（浏览器落盘、别丢会话），grace 秒后还在的按
    进程树强杀 —— 用户已经点过「关闭并清理」，卡住不动才是最差的结果。
    """
    targets = [h for h in holders if h.closable]
    if not targets:
        return 0, 0

    for holder in targets:
        _post_close(holder.pid)

    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if should_stop():
            break
        if not any(psutil.pid_exists(h.pid) for h in targets):
            break
        time.sleep(0.15)

    closed = failed = 0
    for holder in targets:
        if should_stop():
            failed += 1
            continue
        alive = psutil.pid_exists(holder.pid)
        if not alive:
            closed += 1
            continue
        ok, _bad = terminate_tree(holder.pid, timeout=1.5)
        if ok:
            closed += 1
        else:
            failed += 1
    return closed, failed


def available() -> bool:
    """当前平台能不能查占用（只有 Windows 有 Restart Manager）。"""
    return _rm() is not None
