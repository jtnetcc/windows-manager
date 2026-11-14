"""内存优化引擎。

设计原则：默认只做「安全动作」，任何可能影响用户正在使用的程序的操作
都必须显式确认后才执行。
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass, field

import psutil

from . import winmem
from . import applog

# 无论用户怎么勾选，都不允许结束这些进程
CRITICAL_PROCESSES = {
    "system",
    "system idle process",
    "registry",
    "memory compression",
    "memcompression",
    "secure system",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "winlogon.exe",
    "services.exe",
    "lsass.exe",
    "svchost.exe",
    "dwm.exe",
    "explorer.exe",
    "fontdrvhost.exe",
    "sihost.exe",
    "taskhostw.exe",
    "ctfmon.exe",
    "audiodg.exe",
    "spoolsv.exe",
    "msmpeng.exe",
    "securityhealthservice.exe",
}


def visible_window_pids() -> set[int]:
    """收集「拥有顶层窗口」的进程。

    这里不要求窗口可见——很多程序最小化到托盘后窗口只是隐藏了，
    但用户显然还在用它。只要窗口存在且有标题，就算在用。
    """
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    pids: set[int] = set()

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        try:
            if user32.GetWindowTextLengthW(hwnd) > 0:
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value:
                    pids.add(int(pid.value))
        except Exception:
            applog.silenced("optimizer.callback")
        return True

    try:
        user32.EnumWindows(WNDENUMPROC(callback), 0)
    except Exception:
        applog.silenced("optimizer.visible_window_pids")
    return pids


def window_process_tree(pid_parent: dict[int, int]) -> set[int]:
    """把「有窗口的进程」连同它的父进程、子进程一起标记为使用中。

    像 Electron 这类程序，只有主进程持有窗口，渲染进程单独看是「无窗口空闲」，
    直接结束会崩掉用户正在用的程序，所以必须按进程树整体保护。
    """
    protected = set(visible_window_pids())

    # 向上追溯祖先
    for pid in list(protected):
        current = pid
        for _ in range(12):
            parent = pid_parent.get(current)
            if not parent or parent == current or parent in protected:
                break
            protected.add(parent)
            current = parent

    # 向下扩散到所有后代
    changed = True
    while changed:
        changed = False
        for pid, parent in pid_parent.items():
            if parent in protected and pid not in protected:
                protected.add(pid)
                changed = True
    return protected


@dataclass
class OptimizeStep:
    label: str
    ok: bool
    detail: str = ""


@dataclass
class OptimizeResult:
    before_avail: int = 0
    after_avail: int = 0
    freed: int = 0
    duration: float = 0.0
    steps: list[OptimizeStep] = field(default_factory=list)
    needs_admin: bool = False

    @property
    def ok(self) -> bool:
        return any(step.ok for step in self.steps)


def optimize(deep: bool = False) -> OptimizeResult:
    """执行一次温和优化。

    顺序很重要：先把各进程的工作集挤出去，这些页会变成「已修改/待命」，
    再清空待命列表，才能真正变成可用内存。
    """
    started = time.perf_counter()
    winmem.enable_optimize_privileges()

    before = winmem.read_memory()
    result = OptimizeResult(before_avail=before.avail)

    ok, msg = winmem.empty_all_working_sets()
    result.steps.append(OptimizeStep("释放进程工作集", ok, msg))
    if not ok and "权限" in msg:
        result.needs_admin = True

    if deep:
        ok2, msg2 = winmem.flush_modified_list()
        result.steps.append(OptimizeStep("回写已修改页", ok2, msg2))

    ok3, msg3 = winmem.purge_standby_list()
    result.steps.append(OptimizeStep("清理待命缓存", ok3, msg3))
    if not ok3 and "权限" in msg3:
        result.needs_admin = True

    # 给内核一点时间完成页表更新，否则读到的还是旧数字
    time.sleep(0.45)

    after = winmem.read_memory()
    result.after_avail = after.avail
    result.freed = max(0, after.avail - before.avail)
    result.duration = time.perf_counter() - started
    return result


@dataclass
class Candidate:
    pid: int
    name: str
    memory: int
    cpu: float
    reason: str
    active: bool = False


def find_idle_candidates(
    whitelist: set[str] | None = None,
    min_memory: int = 120 * winmem.MB,
    include_active: bool = False,
) -> list[Candidate]:
    """找出「占内存多、但基本没在干活」的进程，供用户手动确认后结束。

    判定条件：内存占用高于阈值、CPU 接近空闲、不在保护名单。
    默认排除一切有窗口的程序（含其父子进程）；`include_active=True` 时
    连正在运行的程序也列出来，由用户自行判断，界面上会明确标注风险。
    """
    whitelist = {w.lower() for w in (whitelist or set())}
    me = psutil.Process().pid

    procs = []
    pid_parent: dict[int, int] = {}
    for proc in psutil.process_iter(
        ["pid", "name", "memory_info", "status", "create_time"]
    ):
        procs.append(proc)
        try:
            pid_parent[proc.pid] = proc.ppid()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    active_all = window_process_tree(pid_parent)
    active_pids = set() if include_active else active_all

    # psutil 的 cpu_percent 需要两次采样才有意义，先预热一轮
    for proc in procs:
        try:
            proc.cpu_percent(None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    time.sleep(0.3)

    found: list[Candidate] = []
    for proc in procs:
        try:
            info = proc.info
            pid = info["pid"]
            name = info["name"] or ""
            lower = name.lower()

            if pid in (0, 4, me) or pid in active_pids:
                continue
            if lower in CRITICAL_PROCESSES or lower in whitelist:
                continue

            mem_info = info["memory_info"]
            if not mem_info or mem_info.rss < min_memory:
                continue

            if info["status"] in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
                continue

            cpu = proc.cpu_percent(None)
            if cpu > 3.0:
                continue

            if mem_info.rss >= 400 * winmem.MB:
                reason = "占用偏高"
            elif include_active and pid in active_all:
                reason = "正在使用"
            else:
                reason = "空闲"
            found.append(
                Candidate(
                    pid=pid,
                    name=name,
                    memory=mem_info.rss,
                    cpu=cpu,
                    reason=reason,
                    active=pid in active_all,
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    found.sort(key=lambda c: c.memory, reverse=True)
    return found


def terminate_processes(pids: list[int], timeout: float = 2.0) -> tuple[int, int]:
    """结束指定进程，返回 (成功数, 失败数)。"""
    ok = failed = 0
    for pid in pids:
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except psutil.TimeoutExpired:
                proc.kill()
            ok += 1
        except Exception:
            failed += 1
    return ok, failed


def terminate_tree(pid: int, timeout: float = 2.0) -> tuple[int, int]:
    """结束进程及其所有子孙进程。

    只结束父进程的话，子进程会被系统重新挂到别的父进程下继续跑
    （很多安装器、启动器都是这个行为）。
    """
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
    except Exception:
        return terminate_processes([pid], timeout)

    # 先杀子进程，父进程退出时就不会再拉起新的
    pids = [child.pid for child in children] + [pid]
    return terminate_processes(pids, timeout)


def process_exe(pid: int) -> str:
    """按需取单个进程的可执行文件路径。

    不能放进 process_list：psutil 查 exe 要为每个进程开句柄，
    200 多个进程会让一次采样从 50ms 涨到 1.5s，纯粹浪费。
    """
    try:
        return psutil.Process(pid).exe() or ""
    except Exception:
        return ""


def process_list(limit: int = 200) -> list[dict]:
    """按内存占用倒序返回进程列表。

    `cpu` 是该进程占系统总 CPU 的百分比（0-100），已除以逻辑核数，
    与任务管理器口径一致。psutil 首次调用返回 0，之后依赖其内部缓存计算。
    """
    rows: list[dict] = []
    me = psutil.Process().pid
    cores = psutil.cpu_count(logical=True) or 1

    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            info = proc.info
            mem_info = info["memory_info"]
            if not mem_info:
                continue
            try:
                cpu = proc.cpu_percent(None) / cores
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                cpu = 0.0
            rows.append(
                {
                    "pid": info["pid"],
                    "name": info["name"] or f"PID {info['pid']}",
                    "memory": mem_info.rss,
                    "cpu": cpu,
                    "is_self": info["pid"] == me,
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    rows.sort(key=lambda r: r["memory"], reverse=True)
    return rows[:limit]
