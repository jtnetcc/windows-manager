"""跨平台系统指标采集。

设计成「采集器 → 不可变快照」：UI 只读快照，不直接碰 psutil。
以后要加 macOS 实现，只需要换掉 MetricsCollector 内部，页面代码不用动。
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass

import psutil

from . import winmem

MB = 1024 * 1024


# ---------------------------------------------------------------- 数据模型


@dataclass(frozen=True)
class CpuMetrics:
    total: float
    per_core: tuple[float, ...]
    freq_mhz: float
    logical: int
    physical: int
    model: str
    load_avg: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass(frozen=True)
class NetworkMetrics:
    down_bps: float
    up_bps: float
    total_recv: int
    total_sent: int
    interfaces: tuple[dict, ...] = ()


@dataclass(frozen=True)
class DiskMetrics:
    read_bps: float
    write_bps: float
    total: int
    used: int
    free: int
    percent: float
    mount: str = ""
    fs: str = ""
    partitions: tuple[dict, ...] = ()


@dataclass(frozen=True)
class GpuMetrics:
    name: str
    utilization: float
    memory_used: int
    memory_total: int

    @property
    def memory_percent(self) -> float:
        return (self.memory_used / self.memory_total * 100) if self.memory_total else 0.0


@dataclass(frozen=True)
class MetricsSnapshot:
    cpu: CpuMetrics
    memory: winmem.MemSnapshot
    network: NetworkMetrics
    disk: DiskMetrics
    gpu: GpuMetrics | None = None
    timestamp: float = 0.0


# ---------------------------------------------------------------- 采集器


def _cpu_model() -> str:
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        )
        name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
        winreg.CloseKey(key)
        return " ".join(str(name).split())
    except Exception:
        import platform

        return platform.processor() or "未知处理器"


def _system_drive() -> str:
    drive = os.environ.get("SystemDrive", "C:")
    return f"{drive}\\"


class MetricsCollector:
    """有状态采集器：保存上一轮计数，用来算网络/磁盘速率。"""

    def __init__(self) -> None:
        self._cpu_model = _cpu_model()
        self._last_net: psutil._common.snetio | None = None
        self._last_iface: dict[str, tuple[int, int]] = {}
        self._last_disk: psutil._common.sdiskio | None = None
        self._last_time: float | None = None
        self._gpu_available = shutil.which("nvidia-smi") is not None
        # 分区容量、网卡地址这类信息几乎不变，没必要每秒去问系统
        self._partitions: tuple[dict, ...] = ()
        self._iface_meta: dict[str, dict] = {}
        self._slow_tick = 0
        # 预热：psutil 的 CPU 百分比需要两次调用才有意义
        psutil.cpu_percent(percpu=True)
        psutil.cpu_percent()

    # -- CPU -------------------------------------------------------
    def _cpu(self) -> CpuMetrics:
        per_core = psutil.cpu_percent(percpu=True)
        total = sum(per_core) / len(per_core) if per_core else 0.0
        try:
            freq = psutil.cpu_freq()
            freq_mhz = freq.current if freq else 0.0
        except Exception:
            freq_mhz = 0.0
        try:
            load = psutil.getloadavg()
        except (AttributeError, OSError):
            load = (0.0, 0.0, 0.0)
        return CpuMetrics(
            total=total,
            per_core=tuple(per_core),
            freq_mhz=freq_mhz,
            logical=psutil.cpu_count(logical=True) or 0,
            physical=psutil.cpu_count(logical=False) or 0,
            model=self._cpu_model,
            load_avg=tuple(load),
        )

    # -- 慢变量 ----------------------------------------------------
    def _refresh_slow(self) -> None:
        """分区容量、网卡地址这类几乎不变的信息，每 5 个周期刷新一次。

        每秒对每个分区做一次 disk_usage()，在有休眠硬盘的机器上会让磁盘
        一直转，没必要。
        """
        self._slow_tick += 1
        if self._slow_tick % 5 != 1:
            return

        parts: list[dict] = []
        try:
            for part in psutil.disk_partitions(all=False):
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                except OSError:
                    # 光驱、未挂载的移动盘会抛错，跳过
                    continue
                if usage.total <= 0:
                    continue
                parts.append(
                    {
                        "mount": part.mountpoint.rstrip("\\") or part.mountpoint,
                        "fs": part.fstype,
                        "total": usage.total,
                        "used": usage.used,
                        "free": usage.free,
                        "percent": usage.percent,
                    }
                )
            parts.sort(key=lambda p: p["mount"].lower())
        except Exception:
            pass
        if parts:
            self._partitions = tuple(parts)

        meta: dict[str, dict] = {}
        try:
            addrs = psutil.net_if_addrs()
            stats = psutil.net_if_stats()
            for name, items in addrs.items():
                ips = [a.address for a in items if a.family == socket.AF_INET]
                stat = stats.get(name)
                meta[name] = {
                    "ip": ips[0] if ips else "",
                    "up": bool(stat.isup) if stat else False,
                    "speed": int(stat.speed) if stat else 0,
                }
        except Exception:
            pass
        if meta:
            self._iface_meta = meta

    # -- 网络 ------------------------------------------------------
    def _network(self, dt: float) -> NetworkMetrics:
        counters = psutil.net_io_counters()
        down = up = 0.0
        if self._last_net is not None and dt > 0:
            down = max(0.0, (counters.bytes_recv - self._last_net.bytes_recv) / dt)
            up = max(0.0, (counters.bytes_sent - self._last_net.bytes_sent) / dt)
        self._last_net = counters

        interfaces = []
        try:
            stats = psutil.net_io_counters(pernic=True)
            previous = self._last_iface or {}
            current: dict[str, tuple[int, int]] = {}
            for name, item in stats.items():
                current[name] = (item.bytes_recv, item.bytes_sent)
                if not (item.bytes_recv or item.bytes_sent):
                    continue
                extra = self._iface_meta.get(name, {})
                down = up = 0.0
                if name in previous and dt > 0:
                    old_recv, old_sent = previous[name]
                    down = max(0.0, (item.bytes_recv - old_recv) / dt)
                    up = max(0.0, (item.bytes_sent - old_sent) / dt)
                interfaces.append(
                    {
                        "name": name,
                        "recv": item.bytes_recv,
                        "sent": item.bytes_sent,
                        "down_bps": down,
                        "up_bps": up,
                        "ip": extra.get("ip", ""),
                        "up": extra.get("up", True),
                        "speed": extra.get("speed", 0),
                    }
                )
            self._last_iface = current
            interfaces.sort(key=lambda x: x["recv"] + x["sent"], reverse=True)
        except Exception:
            pass

        return NetworkMetrics(
            down_bps=down,
            up_bps=up,
            total_recv=counters.bytes_recv,
            total_sent=counters.bytes_sent,
            interfaces=tuple(interfaces[:8]),
        )

    # -- 磁盘 ------------------------------------------------------
    def _disk(self, dt: float) -> DiskMetrics:
        read = write = 0.0
        try:
            counters = psutil.disk_io_counters()
            if counters is not None:
                if self._last_disk is not None and dt > 0:
                    read = max(0.0, (counters.read_bytes - self._last_disk.read_bytes) / dt)
                    write = max(0.0, (counters.write_bytes - self._last_disk.write_bytes) / dt)
                self._last_disk = counters
        except Exception:
            pass

        mount = _system_drive()
        try:
            usage = psutil.disk_usage(mount)
            total, used, free, percent = usage.total, usage.used, usage.free, usage.percent
        except Exception:
            total = used = free = 0
            percent = 0.0

        fs = ""
        try:
            parts = psutil.disk_partitions(all=False)
            for part in parts:
                if part.mountpoint.rstrip("\\").lower() == mount.rstrip("\\").lower():
                    fs = part.fstype
                    break
        except Exception:
            pass

        return DiskMetrics(
            read_bps=read,
            write_bps=write,
            total=total,
            used=used,
            free=free,
            percent=percent,
            mount=mount,
            fs=fs,
            partitions=self._partitions,
        )

    # -- GPU -------------------------------------------------------
    def _gpu(self) -> GpuMetrics | None:
        if not self._gpu_available:
            return None
        try:
            out = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if out.returncode != 0 or not out.stdout.strip():
                return None
            first = out.stdout.strip().splitlines()[0]
            name, util, mem_used, mem_total = [p.strip() for p in first.split(",")]
            return GpuMetrics(
                name=name,
                utilization=float(util),
                memory_used=int(float(mem_used)) * MB,
                memory_total=int(float(mem_total)) * MB,
            )
        except Exception:
            return None

    # -- 对外接口 --------------------------------------------------
    def sample(self) -> MetricsSnapshot:
        now = time.monotonic()
        dt = (now - self._last_time) if self._last_time else 0.0
        self._last_time = now
        self._refresh_slow()

        return MetricsSnapshot(
            cpu=self._cpu(),
            memory=winmem.read_memory(),
            network=self._network(dt),
            disk=self._disk(dt),
            gpu=self._gpu(),
            timestamp=time.time(),
        )


# ---------------------------------------------------------------- 格式化


def fmt_speed(bps: float) -> str:
    if bps >= MB:
        return f"{bps / MB:.1f} MB/s"
    if bps >= 1024:
        return f"{bps / 1024:.0f} KB/s"
    return f"{bps:.0f} B/s"


def fmt_bytes(size: int) -> str:
    """字节格式化。唯一实现在 winmem.fmt_size，这里只保留调用方熟悉的旧名字。"""
    return winmem.fmt_size(size)
