"""物理磁盘清单与通电时间。

**为什么这里破例起 PowerShell。** 这是唯一在真机上验证过的路径：

- `IOCTL_SMART_RCV_DRIVE_DATA`（ATA SMART 直通）在本机返回 err 5，两块 SATA 盘都读不到；
- NVMe 的 SMART 日志页（`IOCTL_STORAGE_QUERY_PROPERTY` +
  `StorageDeviceProtocolSpecificProperty`）返回 err 87，属性 ID、`ProtocolDataOffset`、
  输入补齐、缓冲长度四种参数穷举了 24 组合都不通；
- `Get-StorageReliabilityCounter` 一次就给真值：本机 Samsung 860 EVO 26805 小时、
  ST500LM021 6416 小时。

代价是每次约 1 秒，所以调用方要放后台线程；这里另加 5 分钟缓存。

**NVMe 的通电小时数 Windows 不上报**（本机 WDC SN520 的 PowerOnHours 是空的），
只能显示「—」。这不是没读，是系统真没有。
"""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass

CREATE_NO_WINDOW = 0x08000000
CACHE_SECONDS = 300.0

# 制表符分隔、无表头：字段顺序写死，不受系统语言影响。
# 先强制 UTF-8 输出，否则中文机器上是 GBK，型号里有非 ASCII 就会乱码。
#
# 分隔符必须用 `-join [char]9` 拼，不能在单引号字符串里写 `t ——
# PowerShell 的单引号字符串不处理转义，`t 会原样吐出来两个字符。
_SCRIPT = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    "Get-PhysicalDisk | ForEach-Object {"
    " $r = $_ | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue;"
    " @($_.DeviceId, $_.FriendlyName, $_.SerialNumber, $_.FirmwareVersion,"
    " $_.Size, $_.MediaType, $_.BusType, $r.PowerOnHours) -join [char]9 }"
)


@dataclass
class Disk:
    index: str = ""
    model: str = ""
    serial: str = ""
    firmware: str = ""
    size: int = 0
    media: str = ""       # SSD / HDD / Unspecified
    bus: str = ""         # NVMe / SATA / USB…
    hours: int | None = None   # 通电小时数，NVMe 拿不到为 None

    @property
    def hours_text(self) -> str:
        """一行说完，别太长 —— 前面还挂着序列号和固件，右列放不下会被裁。"""
        if self.hours is None:
            return "—"
        if self.hours >= 8760:
            return f"{self.hours} 小时 / {self.hours / 8760:.1f} 年"
        return f"{self.hours} 小时"

    @property
    def media_text(self) -> str:
        return {"SSD": "固态", "HDD": "机械"}.get(self.media, self.media or "")


_CACHE: tuple[float, list[Disk]] | None = None
_LOCK = threading.Lock()


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8", "mbcs", "gbk"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1", "replace")


def _run() -> str:
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=25,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return _decode(proc.stdout)


def _parse(text: str) -> list[Disk]:
    disks: list[Disk] = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        raw_size = parts[4].strip()
        try:
            size = int(raw_size)
        except ValueError:
            size = 0
        if size <= 0:
            continue  # 离线/虚拟盘，没有容量就没有展示价值
        hours = parts[7].strip()
        # 部分 NVMe 盘（这台机器的 WDC 就是）序列号末尾带一个句点，
        # 是盘自己上报的，不是解析错。留着像打错字，去掉也不丢信息。
        serial = parts[2].strip().rstrip(".")
        disks.append(
            Disk(
                index=parts[0].strip(),
                model=parts[1].strip(),
                serial=serial,
                firmware=parts[3].strip(),
                size=size,
                media=parts[5].strip(),
                bus=parts[6].strip(),
                hours=int(hours) if hours.isdigit() else None,
            )
        )
    disks.sort(key=lambda d: d.index)
    return disks


def inventory(use_cache: bool = True) -> list[Disk]:
    """列出所有物理盘。失败返回空列表，不抛异常。"""
    global _CACHE

    if use_cache:
        with _LOCK:
            if _CACHE is not None and time.time() - _CACHE[0] < CACHE_SECONDS:
                return _CACHE[1]

    disks = _parse(_run())

    with _LOCK:
        _CACHE = (time.time(), disks)
    return disks


def clear_cache() -> None:
    global _CACHE
    with _LOCK:
        _CACHE = None
