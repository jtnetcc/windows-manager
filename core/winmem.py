"""Windows 内存信息采集与系统 API 封装。

所有底层调用都集中在这里，方便统一处理错误码和权限问题。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

# ---------------------------------------------------------------- 结构体定义


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class PERFORMANCE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("CommitTotal", ctypes.c_size_t),
        ("CommitLimit", ctypes.c_size_t),
        ("CommitPeak", ctypes.c_size_t),
        ("PhysicalTotal", ctypes.c_size_t),
        ("PhysicalAvailable", ctypes.c_size_t),
        ("SystemCache", ctypes.c_size_t),
        ("KernelTotal", ctypes.c_size_t),
        ("KernelPaged", ctypes.c_size_t),
        ("KernelNonpaged", ctypes.c_size_t),
        ("PageSize", ctypes.c_size_t),
        ("HandleCount", wintypes.DWORD),
        ("ProcessCount", wintypes.DWORD),
        ("ThreadCount", wintypes.DWORD),
    ]


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class MEMORY_LIST_INFORMATION(ctypes.Structure):
    """内核页列表统计，用于还原任务管理器的「使用中/待命/可用」口径。"""

    _fields_ = [
        ("ZeroPageCount", ctypes.c_size_t),
        ("FreePageCount", ctypes.c_size_t),
        ("ModifiedPageCount", ctypes.c_size_t),
        ("ModifiedNoWritePageCount", ctypes.c_size_t),
        ("BadPageCount", ctypes.c_size_t),
        ("PageCountByPriority", ctypes.c_size_t * 8),
        ("RepurposedPagesByPriority", ctypes.c_size_t * 8),
        ("ModifiedPageCountPageFile", ctypes.c_size_t),
    ]


class LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]


class TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [
        ("PrivilegeCount", wintypes.DWORD),
        ("Privileges", LUID_AND_ATTRIBUTES * 1),
    ]


# ---------------------------------------------------------------- 函数签名

kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MEMORYSTATUSEX)]
kernel32.GlobalMemoryStatusEx.restype = wintypes.BOOL

psapi.GetPerformanceInfo.argtypes = [
    ctypes.POINTER(PERFORMANCE_INFORMATION),
    wintypes.DWORD,
]
psapi.GetPerformanceInfo.restype = wintypes.BOOL

kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

ntdll.NtSetSystemInformation.argtypes = [
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.c_ulong,
]
ntdll.NtSetSystemInformation.restype = ctypes.c_long

ntdll.NtQuerySystemInformation.argtypes = [
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.c_ulong,
    ctypes.POINTER(ctypes.c_ulong),
]
ntdll.NtQuerySystemInformation.restype = ctypes.c_long

advapi32.OpenProcessToken.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.HANDLE),
]
advapi32.OpenProcessToken.restype = wintypes.BOOL

advapi32.LookupPrivilegeValueW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    ctypes.POINTER(LUID),
]
advapi32.LookupPrivilegeValueW.restype = wintypes.BOOL

advapi32.AdjustTokenPrivileges.argtypes = [
    wintypes.HANDLE,
    wintypes.BOOL,
    ctypes.POINTER(TOKEN_PRIVILEGES),
    wintypes.DWORD,
    ctypes.c_void_p,
    ctypes.c_void_p,
]
advapi32.AdjustTokenPrivileges.restype = wintypes.BOOL

# ---------------------------------------------------------------- 常量

TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_QUERY = 0x0008
SE_PRIVILEGE_ENABLED = 0x0002

SYSTEM_MEMORY_LIST_INFORMATION = 80
MEMORY_EMPTY_WORKING_SETS = 2
MEMORY_FLUSH_MODIFIED_LIST = 3
MEMORY_PURGE_STANDBY_LIST = 4

GB = 1024 ** 3
MB = 1024 ** 2


# ---------------------------------------------------------------- 数据模型


@dataclass(frozen=True)
class MemSnapshot:
    """某一时刻的物理内存快照（单位：字节）。

    口径与任务管理器一致：
    `used`（使用中）+ `standby`（待命）+ `modified`（已修改）+ `free`（空闲）= `total`。
    """

    total: int
    avail: int
    used: int
    standby: int
    modified: int
    free: int
    percent: float
    commit_used: int
    commit_limit: int
    process_count: int
    thread_count: int
    handle_count: int

    @property
    def cache(self) -> int:
        """可被系统回收的部分：待命 + 已修改。"""
        return self.standby + self.modified


def fmt_size(size: int) -> str:
    """把字节数格式化成人类可读的字符串。

    全项目唯一的字节格式化实现：清理、体检、软件、进程各页面都走这里，
    避免同一份数据在不同页面显示成 1.2 GB / 1.23 GB 两个样子。
    """
    if size >= GB:
        return f"{size / GB:.2f} GB"
    if size >= MB:
        return f"{size / MB:.0f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


# ---------------------------------------------------------------- 采集接口


def _align(
    total: int, avail: int, free: int, modified: int, standby: int
) -> tuple[int, int, int]:
    """把「空闲 / 待命 / 已修改」对齐到 Windows 的 avail 口径。

    内核页列表在没开 `SeProfileSingleProcessPrivilege` 时会「部分字段有效」：
    FreePageCount 是对的，PageCountByPriority（待命）却只回来一小截。用户截图上
    就出现过「使用中 12.83 GB + 可用 11.40 GB = 24 GB，物理内存只有 15.7 GB」，
    自相矛盾，构成图里待命那块还特别窄。

    而 avail 里本来就含着待命（avail = 空闲 + 待命 + 已修改），是现成的权威值，
    反推即可，不用去抢特权。返回 (free, standby, modified)。
    """
    if free + standby + modified == 0:
        return avail, 0, 0
    implied = avail - free - modified
    if implied > standby:
        standby = implied
    if free + standby + modified > total:
        standby = max(0, total - free - modified)
    return free, standby, modified


def read_memory() -> MemSnapshot:
    """读取当前物理内存状态。"""
    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError(ctypes.get_last_error(), "GlobalMemoryStatusEx 调用失败")

    perf = PERFORMANCE_INFORMATION()
    perf.cb = ctypes.sizeof(PERFORMANCE_INFORMATION)
    has_perf = bool(psapi.GetPerformanceInfo(ctypes.byref(perf), perf.cb))

    total = int(status.ullTotalPhys)
    avail = int(status.ullAvailPhys)
    page_size = int(perf.PageSize) if has_perf and perf.PageSize else 4096

    # 优先使用内核页列表统计，这样「使用中 / 待命 / 空闲」和任务管理器一致
    lists = MEMORY_LIST_INFORMATION()
    returned = ctypes.c_ulong(0)
    nt_status = ntdll.NtQuerySystemInformation(
        SYSTEM_MEMORY_LIST_INFORMATION,
        ctypes.byref(lists),
        ctypes.sizeof(lists),
        ctypes.byref(returned),
    )

    free = standby = modified = 0
    if nt_status == 0:
        free = int(lists.FreePageCount) * page_size
        standby = int(sum(lists.PageCountByPriority)) * page_size
        modified = (
            int(lists.ModifiedPageCount) + int(lists.ModifiedNoWritePageCount)
        ) * page_size
        # 数值明显不合理时回退到粗略口径
        if free + standby + modified > total:
            free, standby, modified = avail, 0, 0

    if free + standby + modified == 0:
        free, standby, modified = avail, 0, 0

    # 「已用」只认 GlobalMemoryStatusEx 的 avail —— 这是系统给的权威口径，
    # 和任务管理器的「已使用」完全一致。
    #
    # 之前用 total - free - standby - modified 自己算，问题是那三个值来自
    # NtQuerySystemInformation，需要 SeProfileSingleProcessPrivilege：
    # 特权没启用时内核可能返回「部分字段有效」的结果，standby 少算几个 GB，
    # used 就虚高 —— 用户看到 80%，任务管理器却是 70%。
    # 拿特权去凑一个本来就有现成答案的数字，纯属自找麻烦。
    used = max(0, total - avail)

    # free / standby / modified 只用于界面上的细分展示，不参与占用率计算。
    # 页列表读不到时退回「可用即空闲」，至少不会给出错的数字。
    free, standby, modified = _align(total, avail, free, modified, standby)

    if has_perf:
        commit_used = int(perf.CommitTotal * page_size)
        proc_count = int(perf.ProcessCount)
        thread_count = int(perf.ThreadCount)
        handle_count = int(perf.HandleCount)
    else:
        commit_used = int(status.ullTotalPageFile - status.ullAvailPageFile)
        proc_count = thread_count = handle_count = 0

    return MemSnapshot(
        total=total,
        avail=avail,
        used=used,
        standby=standby,
        modified=modified,
        free=free,
        # 百分比按「使用中」计算，与任务管理器口径一致。
        # 待命缓存属于随时可回收的部分，算进占用率会让数字虚高到 100%。
        percent=(used / total * 100.0) if total else 0.0,
        commit_used=commit_used,
        commit_limit=int(status.ullTotalPageFile),
        process_count=proc_count,
        thread_count=thread_count,
        handle_count=handle_count,
    )


# ---------------------------------------------------------------- 权限


def is_admin() -> bool:
    """当前进程是否具备管理员权限。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def enable_privilege(name: str) -> bool:
    """为自己启用一个令牌特权，例如 SeProfileSingleProcessPrivilege。"""
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(),
        TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
        ctypes.byref(token),
    ):
        return False
    try:
        luid = LUID()
        if not advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
            return False
        tp = TOKEN_PRIVILEGES()
        tp.PrivilegeCount = 1
        tp.Privileges[0].Luid = luid
        tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
        if not advapi32.AdjustTokenPrivileges(
            token, False, ctypes.byref(tp), 0, None, None
        ):
            return False
        return ctypes.get_last_error() == 0
    finally:
        kernel32.CloseHandle(token)


def enable_optimize_privileges() -> None:
    """优化操作需要的一组特权。"""
    for name in (
        "SeProfileSingleProcessPrivilege",
        "SeIncreaseQuotaPrivilege",
        "SeDebugPrivilege",
    ):
        enable_privilege(name)


# ---------------------------------------------------------------- 优化动作


def set_memory_list_command(command: int) -> tuple[bool, str]:
    """向内核下发内存列表命令，返回 (是否成功, 错误说明)。"""
    code = ctypes.c_ulong(command)
    status = ntdll.NtSetSystemInformation(
        SYSTEM_MEMORY_LIST_INFORMATION,
        ctypes.byref(code),
        ctypes.sizeof(code),
    )
    if status == 0:
        return True, ""
    # NTSTATUS 常见错误：0xC0000061 权限不足，0xC000000D 参数错误
    if status & 0xFFFFFFFF == 0xC0000061:
        return False, "权限不足，需要以管理员身份运行"
    return False, f"NTSTATUS 0x{status & 0xFFFFFFFF:08X}"


def empty_all_working_sets() -> tuple[bool, str]:
    """一次性清空所有进程的工作集。"""
    return set_memory_list_command(MEMORY_EMPTY_WORKING_SETS)


def purge_standby_list() -> tuple[bool, str]:
    """清空待命列表（真正的缓存回收）。"""
    return set_memory_list_command(MEMORY_PURGE_STANDBY_LIST)


def flush_modified_list() -> tuple[bool, str]:
    """把已修改页刷回磁盘。"""
    return set_memory_list_command(MEMORY_FLUSH_MODIFIED_LIST)
