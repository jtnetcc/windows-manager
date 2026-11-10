"""本机硬件与系统参数。

数据来源全部是「不用管理员、不依赖 WMI」的路子：

- **SMBIOS 固件表**（`GetSystemFirmwareTable('RSMB')`）拿主板、BIOS、处理器、内存插槽
  的真实型号和频率。这是主板固件里的原始数据，比 WMI 快得多，也不用起进程。
- **注册表**拿系统版本、CPU 名称、显卡型号 —— 这些 Windows 自己已经缓存好了。
- **ctypes** 拿分辨率、显示器数量。

WMI（`wmic` / `Get-CimInstance`）一律不用：Win11 24H2 已经移除 wmic，
而且每次查询要起一个进程，几百毫秒起步。
"""

from __future__ import annotations

import ctypes
import os
import platform
import socket
import struct
import time
from ctypes import wintypes
from dataclasses import dataclass, field

RSMB = 0x52534D42  # 'RSMB'，原始 SMBIOS 表


# ---------------------------------------------------------------- SMBIOS


def _smbios_table() -> bytes:
    """读原始 SMBIOS 表。拿不到返回空字节串。

    注意：`GetSystemFirmwareTable('RSMB')` 返回的不是裸表，前面还有一个
    8 字节的 `RawSMBIOSData` 头：

        BYTE  Used20CallingMethod
        BYTE  SMBIOSMajorVersion
        BYTE  SMBIOSMinorVersion
        BYTE  DmiRevision
        DWORD Length
        BYTE  SMBIOSTableData[]

    直接从偏移 0 开始按结构体解析会把第一个字节当 type、第二个当 length，
    结果 length=3 < 4 直接跳出循环，什么都解析不出来。
    """
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetSystemFirmwareTable.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.GetSystemFirmwareTable.restype = wintypes.UINT

        size = kernel32.GetSystemFirmwareTable(RSMB, 0, None, 0)
        if not size:
            return b""
        buffer = ctypes.create_string_buffer(size)
        got = kernel32.GetSystemFirmwareTable(RSMB, 0, buffer, size)
        if not got:
            return b""
        raw = buffer.raw[:got]
    except Exception:
        return b""

    if len(raw) < 8:
        return b""
    length = struct.unpack_from("<I", raw, 4)[0]
    body = raw[8:]
    if 0 < length <= len(body):
        return body[:length]
    return body


def _read_strings(table: bytes, start: int) -> tuple[list[str], int]:
    """读结构体后面的字符串区，返回 (字符串列表, 下一个结构体的起始偏移)。

    每个字符串以 \\0 结尾，字符串集合以「空字符串」收尾。这里有个坑：
    - 有 N≥1 个字符串时，最后一个字符串的 \\0 后面还有 **一个** 收尾 \\0
    - 一个字符串都没有时，整个字符串区是 **两个** 连续的 \\0

    两种情况都表现为「读到 pos 处就是 0」，但需要跳过的字节数不同。
    判据看后一个字节：还是 0 说明是空集合，跳 2；否则跳 1。
    （type 0 的 BIOS 结构只出现在表首，所以不会出现「跳 2 误吃掉下一个结构」）
    """
    out: list[str] = []
    pos = start
    total = len(table)
    while pos < total:
        end = table.find(b"\x00", pos)
        if end < 0:
            return out, total
        if end == pos:
            if pos + 1 < total and table[pos + 1] == 0:
                return out, pos + 2
            return out, pos + 1
        out.append(_decode(table[pos:end]))
        pos = end + 1
    return out, total


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace").strip()


def _pick(strings: list[str], index: int) -> str:
    if index <= 0 or index > len(strings):
        return ""
    return strings[index - 1]


def _iter_structures(table: bytes):
    """逐个产出 (类型, 格式化区, 字符串列表)。"""
    offset = 0
    total = len(table)
    while offset + 4 <= total:
        stype = table[offset]
        length = table[offset + 1]
        if length < 4 or offset + length > total:
            break
        formatted = table[offset : offset + length]
        strings, next_offset = _read_strings(table, offset + length)
        yield stype, formatted, strings
        if next_offset <= offset:
            break
        offset = next_offset


@dataclass
class MemoryModule:
    slot: str = ""
    size: int = 0          # 字节
    speed: int = 0         # MT/s
    manufacturer: str = ""
    part: str = ""


@dataclass
class SysInfo:
    # 系统
    os_name: str = ""
    os_version: str = ""
    os_build: str = ""
    install_date: str = ""
    hostname: str = ""
    username: str = ""
    architecture: str = ""
    # 处理器
    cpu_name: str = ""
    cpu_cores: int = 0
    cpu_threads: int = 0
    cpu_max_mhz: int = 0
    cpu_socket: str = ""
    cpu_manufacturer: str = ""
    # 内存
    memory_total: int = 0
    memory_modules: list[MemoryModule] = field(default_factory=list)
    # 主板 / BIOS
    board_vendor: str = ""
    board_model: str = ""
    bios_vendor: str = ""
    bios_version: str = ""
    bios_date: str = ""
    # 显卡
    gpus: list[str] = field(default_factory=list)
    # 显示
    screen: str = ""
    monitors: int = 1
    # 网络
    ip: str = ""
    mac: str = ""

    @property
    def memory_slots(self) -> int:
        return len(self.memory_modules)

    @property
    def memory_used_slots(self) -> int:
        return sum(1 for m in self.memory_modules if m.size > 0)


def _parse_smbios(info: SysInfo) -> None:
    table = _smbios_table()
    if not table:
        return

    for stype, data, strings in _iter_structures(table):
        try:
            if stype == 0 and len(data) >= 9:  # BIOS
                info.bios_vendor = _pick(strings, data[4])
                info.bios_version = _pick(strings, data[5])
                info.bios_date = _pick(strings, data[8])
            elif stype == 1 and len(data) >= 6:  # System
                if not info.board_vendor:
                    info.board_vendor = _pick(strings, data[4])
            elif stype == 2 and len(data) >= 6:  # Baseboard
                info.board_vendor = _pick(strings, data[4])
                info.board_model = _pick(strings, data[5])
            elif stype == 4 and len(data) >= 0x26:  # Processor
                info.cpu_manufacturer = _pick(strings, data[7])
                version = _pick(strings, data[0x10])
                if version and not info.cpu_name:
                    info.cpu_name = version
                info.cpu_socket = _pick(strings, data[4])
                # 有些主板的 Max Speed 字段是错的（比如写成 8300），
                # 注册表里的 ~MHz 更可信，只在拿不到时才用 SMBIOS
                if not info.cpu_max_mhz:
                    info.cpu_max_mhz = struct.unpack_from("<H", data, 0x14)[0]
                info.cpu_cores = data[0x23]
                info.cpu_threads = data[0x25]
            elif stype == 17 and len(data) >= 0x15:  # Memory Device
                size_mb = struct.unpack_from("<H", data, 0x0C)[0]
                if size_mb == 0xFFFF:
                    continue  # 未知
                if size_mb == 0x7FFF and len(data) >= 0x20:
                    size_mb = struct.unpack_from("<I", data, 0x1C)[0]
                speed = struct.unpack_from("<H", data, 0x15)[0]
                info.memory_modules.append(
                    MemoryModule(
                        slot=_pick(strings, data[0x10]),
                        size=int(size_mb) * 1024 * 1024,
                        speed=speed,
                        manufacturer=_pick(strings, data[0x17]),
                        part=_pick(strings, data[0x1A]),
                    )
                )
        except (struct.error, IndexError):
            continue


# ---------------------------------------------------------------- 注册表


def _reg(path: str, name: str, root=None):
    import winreg

    root = root or winreg.HKEY_LOCAL_MACHINE
    try:
        with winreg.OpenKey(root, path) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return value
    except OSError:
        return None


def _os_info(info: SysInfo) -> None:
    path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    product = _reg(path, "ProductName") or ""
    build = _reg(path, "CurrentBuild") or ""
    ubr = _reg(path, "UBR")
    display = _reg(path, "DisplayVersion") or ""

    # Win11 的注册表里 ProductName 仍写着 "Windows 10"，只能靠 build 号判断
    try:
        if int(build) >= 22000:
            product = product.replace("Windows 10", "Windows 11")
    except (TypeError, ValueError):
        pass

    info.os_name = product or platform.system()
    info.os_version = display
    info.os_build = f"{build}.{ubr}" if ubr else str(build)
    info.architecture = platform.machine()

    stamp = _reg(path, "InstallDate")
    if isinstance(stamp, int) and stamp > 0:
        info.install_date = time.strftime("%Y-%m-%d", time.localtime(stamp))


def _cpu_info(info: SysInfo) -> None:
    name = _reg(
        r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString"
    )
    if name:
        info.cpu_name = " ".join(str(name).split())
    freq = _reg(
        r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "~MHz"
    )
    if isinstance(freq, int) and not info.cpu_max_mhz:
        info.cpu_max_mhz = freq


def _gpu_info(info: SysInfo) -> None:
    import winreg

    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as key:
            for index in range(8):
                try:
                    with winreg.OpenKey(key, f"{index:04d}") as sub:
                        desc, _ = winreg.QueryValueEx(sub, "DriverDesc")
                except OSError:
                    continue
                desc = str(desc).strip()
                # 过滤掉虚拟显示器 / 远程桌面这类伪设备
                if not desc or desc in info.gpus:
                    continue
                if any(k in desc.lower() for k in ("remote", "basic display", "idd")):
                    continue
                info.gpus.append(desc)
    except OSError:
        pass


def _display_info(info: SysInfo) -> None:
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        width = user32.GetSystemMetrics(0)
        height = user32.GetSystemMetrics(1)
        monitors = user32.GetSystemMetrics(80)  # SM_CMONITORS
        if width and height:
            info.screen = f"{width} × {height}"
        if monitors:
            info.monitors = int(monitors)
    except Exception:
        pass


def _network_info(info: SysInfo) -> None:
    try:
        info.hostname = socket.gethostname()
    except OSError:
        info.hostname = ""
    info.username = os.environ.get("USERNAME", "")

    try:
        import uuid

        # getnode() 优先取真实网卡 MAC，取不到时返回一个随机值（第 41 位为 1）
        node = uuid.getnode()
        if not (node >> 40) & 1:
            info.mac = ":".join(f"{(node >> (8 * i)) & 0xFF:02X}" for i in range(5, -1, -1))
    except Exception:
        pass

    try:
        # 连一个不存在的地址只为让系统选出「出口网卡」，不会真的发包
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.settimeout(0.2)
        probe.connect(("10.255.255.255", 1))
        info.ip = probe.getsockname()[0]
        probe.close()
    except OSError:
        info.ip = ""


def _memory_total(info: SysInfo) -> None:
    try:
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

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            info.memory_total = int(status.ullTotalPhys)
    except Exception:
        pass


def collect() -> SysInfo:
    """采集全部系统信息。单步失败不影响其他项。"""
    info = SysInfo()
    for step in (
        _os_info,
        _cpu_info,
        _parse_smbios,
        _gpu_info,
        _display_info,
        _network_info,
        _memory_total,
    ):
        try:
            step(info)
        except Exception:
            continue
    return info


def fmt_size(size: int) -> str:
    """字节格式化。统一实现在 winmem.fmt_size。"""
    from .winmem import fmt_size as _fmt

    return _fmt(size)
