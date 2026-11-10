"""磁盘健康探测（纯 ctypes，不依赖外部程序）。

三件事：
    1. 介质类型：SSD / HDD —— 问设备「寻道是否有代价」
    2. SMART 预警   —— IOCTL_STORAGE_PREDICT_FAILURE
    3. 分区 4K 对齐 —— 分区起始偏移能否被 4096 整除

用 DeviceIoControl 而不是 PowerShell / wmic，原因有三：快（毫秒级）、
没有编码问题、不需要管理员权限（除 SMART 外）。
"""

from __future__ import annotations

import ctypes
import struct
from ctypes import wintypes
from dataclasses import dataclass

GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
IOCTL_STORAGE_PREDICT_FAILURE = 0x002D1100
IOCTL_DISK_GET_PARTITION_INFO_EX = 0x00070048

STORAGE_DEVICE_SEEK_PENALTY_PROPERTY = 7
PROPERTY_STANDARD_QUERY = 0


@dataclass
class DiskProbe:
    name: str
    media: str = "Unknown"  # SSD / HDD / Unknown
    smart_ok: bool | None = None
    aligned: bool | None = None
    offset: int = 0


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
_kernel32.CreateFileW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.DeviceIoControl.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p,
]
_kernel32.DeviceIoControl.restype = wintypes.BOOL


def _open(path: str) -> int | None:
    """打开设备句柄。

    先试 GENERIC_READ，失败再退到 0 权限 —— 卷句柄做
    IOCTL_DISK_GET_PARTITION_INFO_EX 这类查询不需要读权限，
    普通用户也能拿到分区信息。
    """
    for access in (GENERIC_READ, 0):
        handle = _kernel32.CreateFileW(
            path,
            access,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        if handle and handle != INVALID_HANDLE_VALUE:
            return handle
    return None


def _ioctl(handle: int, code: int, in_buf: bytes, out_size: int) -> bytes | None:
    in_buffer = ctypes.create_string_buffer(in_buf, len(in_buf))
    out_buffer = ctypes.create_string_buffer(out_size)
    returned = wintypes.DWORD(0)
    ok = _kernel32.DeviceIoControl(
        handle,
        code,
        ctypes.cast(in_buffer, ctypes.c_void_p),
        len(in_buf),
        ctypes.cast(out_buffer, ctypes.c_void_p),
        out_size,
        ctypes.byref(returned),
        None,
    )
    if not ok:
        return None
    return out_buffer.raw[: returned.value]


def seek_penalty(physical_drive: str = r"\\.\PhysicalDrive0") -> str:
    """SSD / HDD 判定。

    原理：固态盘寻道没有代价（IncursSeekPenalty = 0），机械盘有。
    这是 Windows 自己用来区分两者的方式，比看型号字符串可靠。
    """
    handle = _open(physical_drive)
    if handle is None:
        return "Unknown"
    try:
        # STORAGE_PROPERTY_QUERY 是 12 字节：PropertyId(4) + QueryType(4) +
        # AdditionalParameters[1] 补齐到 4。传 8 字节会被驱动判为非法参数，
        # DeviceIoControl 直接失败（这个坑不明显，记一笔）。
        query = struct.pack(
            "<II4s", STORAGE_DEVICE_SEEK_PENALTY_PROPERTY, PROPERTY_STANDARD_QUERY, b""
        )
        data = _ioctl(handle, IOCTL_STORAGE_QUERY_PROPERTY, query, 16)
        if not data or len(data) < 12:
            return "Unknown"
        # DEVICE_SEEK_PENALTY_DESCRIPTOR { Version(4), Size(4), IncursSeekPenalty(1) }
        incurs = data[8]
        return "HDD" if incurs else "SSD"
    finally:
        _kernel32.CloseHandle(handle)


def predict_failure(physical_drive: str = r"\\.\PhysicalDrive0") -> bool | None:
    """SMART 预警。True = 健康，None = 查不到（多半是权限不够）。"""
    handle = _open(physical_drive)
    if handle is None:
        return None
    try:
        data = _ioctl(handle, IOCTL_STORAGE_PREDICT_FAILURE, b"", 516)
        if not data or len(data) < 4:
            return None
        failing = struct.unpack_from("<I", data, 0)[0]
        return failing == 0
    finally:
        _kernel32.CloseHandle(handle)


def partition_offset(drive_letter: str = "C:") -> int:
    """分区起始偏移（字节）。取不到返回 0。"""
    handle = _open("\\\\.\\" + drive_letter.rstrip("\\"))
    if handle is None:
        return 0
    try:
        data = _ioctl(handle, IOCTL_DISK_GET_PARTITION_INFO_EX, b"", 160)
        if not data or len(data) < 16:
            return 0
        # PARTITION_INFORMATION_EX: PartitionStyle(4) + StartingOffset(8) + ...
        return struct.unpack_from("<q", data, 8)[0]
    finally:
        _kernel32.CloseHandle(handle)


def probe() -> list[DiskProbe]:
    """探测所有物理盘的介质类型与 SMART，外加系统盘 4K 对齐。"""
    results: list[DiskProbe] = []
    for index in range(8):
        name = rf"\\.\PhysicalDrive{index}"
        if _open(name) is None:
            # 打不开就说明这个序号没有盘（也可能是权限不足，直接跳过）
            if index >= 2:
                break
            continue
        results.append(
            DiskProbe(
                name=f"磁盘 {index}",
                media=seek_penalty(name),
                smart_ok=predict_failure(name),
            )
        )

    offset = partition_offset()
    if results:
        results[0].offset = offset
        results[0].aligned = (offset % 4096 == 0) if offset else None
    return results


def summary() -> dict:
    """给评分引擎用的汇总。"""
    try:
        disks = probe()
    except Exception:
        disks = []
    if not disks:
        return {
            "media": "Unknown",
            "smart_ok": None,
            "aligned": None,
            "count": 0,
            "has_ssd": False,
        }
    has_ssd = any(d.media == "SSD" for d in disks)
    has_hdd = any(d.media == "HDD" for d in disks)
    media = "SSD" if has_ssd and not has_hdd else ("HDD" if has_hdd and not has_ssd else "混合")
    smart_values = [d.smart_ok for d in disks if d.smart_ok is not None]
    return {
        "media": media,
        "smart_ok": all(smart_values) if smart_values else None,
        "aligned": disks[0].aligned,
        "count": len(disks),
        "has_ssd": has_ssd,
    }
