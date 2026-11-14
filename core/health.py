"""电脑健康度评分。

11 项加权因子，权重和为 100。`compute()` 是**纯函数**，不碰任何系统 API，
方便单测；数据采集在 `collect()` 里，慢的部分（磁盘探测）带缓存。

未接入的因子标记为 `measured=False`，最终得分按**已测权重**归一化 ——
这样随着清理、重复文件等模块逐步接入，分数会自然变得更完整，
而不是一开始就凭空给个 0 分把总分拖死。
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field, replace
from . import applog

GB = 1024 ** 3

LEVELS = (
    (85, "优秀", "green"),
    (70, "良好", "blue"),
    (50, "一般", "amber"),
    (30, "较差", "orange"),
    (0, "危险", "red"),
)


@dataclass
class Factor:
    key: str
    label: str
    weight: int
    score: float
    detail: str
    measured: bool = True

    @property
    def max(self) -> int:
        return self.weight

    @property
    def ratio(self) -> float:
        return (self.score / self.weight) if self.weight else 0.0


@dataclass
class HealthSnapshot:
    system_free: int = 0
    system_total: int = 0
    junk_bytes: int = 0
    dup_bytes: int = 0
    social_bytes: int = 0
    registry_count: int = 0
    startup_count: int = 0
    media: str = "Unknown"
    trim_enabled: bool | None = None
    smart_ok: bool | None = None
    aligned: bool | None = None
    file_system: str = ""
    update_backlog: int = 0
    releasable: int = 0
    memory_percent: float = 0.0
    # 1 表示「一块盘一个分区」的整机/笔记本常见出厂布局。
    # 这种机器没有「把文件挪到 D 盘」这条路，建议要换个说法。
    partition_count: int = 0
    # 哪些因子已经有真实数据了
    has_junk: bool = False
    has_dup: bool = False
    has_social: bool = False
    has_registry: bool = False
    has_update: bool = False


@dataclass
class Advice:
    """一条优化建议。

    `page` 是对应的页面 key（与 UI 的 NAV_ITEMS 一致），空字符串表示这项目前
    没有「一键去处理」的入口 —— 比如磁盘 SMART 告警、系统盘容量这类硬件事实，
    硬塞一个跳转按钮只会让人点进去发现什么也做不了。
    """

    text: str
    page: str = ""


@dataclass
class HealthReport:
    score: int
    level: str
    level_color: str
    factors: list[Factor] = field(default_factory=list)
    advice: list[Advice] = field(default_factory=list)
    measured_weight: int = 0
    total_weight: int = 100
    snapshot: HealthSnapshot | None = None
    scanned_at: float = 0.0

    @property
    def measured_count(self) -> int:
        return sum(1 for f in self.factors if f.measured)

    @property
    def complete(self) -> bool:
        return self.measured_count == len(self.factors)


# ---------------------------------------------------------------- 打分曲线


def larger_better(value: float, full_at: float, zero_at: float, weight: int) -> float:
    """越大越好：≥full_at 满分，≤zero_at 零分，中间线性。"""
    if value >= full_at:
        return weight
    if value <= zero_at:
        return 0.0
    return weight * (value - zero_at) / (full_at - zero_at)


def smaller_better(value: float, full_at: float, zero_at: float, weight: int) -> float:
    """越小越好：≤full_at 满分，≥zero_at 零分，中间线性。"""
    if value <= full_at:
        return weight
    if value >= zero_at:
        return 0.0
    return weight * (zero_at - value) / (zero_at - full_at)


def _size(size: float) -> str:
    """小于 1 GB 用 MB 显示 —— 51 MB 写成「0.0 GB」会让人以为没扫到东西。"""
    if size >= GB:
        return f"{size / GB:.1f} GB"
    if size >= 1024 ** 2:
        return f"{size / 1024 ** 2:.0f} MB"
    return f"{size / 1024:.0f} KB"


# ---------------------------------------------------------------- 评分


def compute(snapshot: HealthSnapshot) -> HealthReport:
    factors: list[Factor] = []

    # 1. 系统盘剩余空间 28 —— 百分比与绝对值取较差者
    free_gb = snapshot.system_free / GB
    total_gb = snapshot.system_total / GB
    percent = (snapshot.system_free / snapshot.system_total * 100) if snapshot.system_total else 0
    free_score = min(
        larger_better(percent, 30, 10, 28),
        larger_better(free_gb, 20, 5, 28),
    )
    factors.append(
        Factor(
            "free",
            "系统盘剩余空间",
            28,
            free_score,
            f"剩余 {free_gb:.1f} GB · {percent:.0f}%"
            + ("（仅 1 个分区）" if snapshot.partition_count == 1 else ""),
            measured=snapshot.system_total > 0,
        )
    )

    # 2. 系统盘容量 5
    factors.append(
        Factor(
            "cap",
            "系统盘总容量",
            5,
            larger_better(total_gb, 120, 64, 5),
            f"{total_gb:.0f} GB",
            measured=snapshot.system_total > 0,
        )
    )

    # 3. 垃圾文件 12
    factors.append(
        Factor(
            "junk",
            "垃圾文件体积",
            12,
            smaller_better(snapshot.junk_bytes, 1 * GB, 15 * GB, 12),
            _size(snapshot.junk_bytes) if snapshot.has_junk else "待扫描",
            measured=snapshot.has_junk,
        )
    )

    # 4. 重复文件 8
    factors.append(
        Factor(
            "dup",
            "重复文件体积",
            8,
            smaller_better(snapshot.dup_bytes, 0.5 * GB, 10 * GB, 8),
            _size(snapshot.dup_bytes) if snapshot.has_dup else "待扫描",
            measured=snapshot.has_dup,
        )
    )

    # 5. 社交缓存 8
    factors.append(
        Factor(
            "social",
            "社交软件缓存",
            8,
            smaller_better(snapshot.social_bytes, 1 * GB, 15 * GB, 8),
            _size(snapshot.social_bytes) if snapshot.has_social else "未扫描",
            measured=snapshot.has_social,
        )
    )

    # 6. 注册表冗余 4
    factors.append(
        Factor(
            "reg",
            "注册表冗余项",
            4,
            smaller_better(snapshot.registry_count, 50, 500, 4),
            f"{snapshot.registry_count} 项" if snapshot.has_registry else "未扫描",
            measured=snapshot.has_registry,
        )
    )

    # 7. 启动项数量 7
    factors.append(
        Factor(
            "startup",
            "启动项数量",
            7,
            smaller_better(snapshot.startup_count, 5, 20, 7),
            f"{snapshot.startup_count} 个",
            measured=snapshot.startup_count > 0,
        )
    )

    # 8. 磁盘健康 11
    disk_score = 11.0
    detail = "正常"
    measured = True
    if snapshot.media == "Unknown":
        disk_score = 8.0
        detail = "健康（类型未知）"
        measured = False
    elif snapshot.media == "SSD":
        detail = "SSD"
        if snapshot.trim_enabled is False:
            disk_score = 6.0
            detail = "SSD · 未开启 TRIM"
        elif snapshot.trim_enabled is True:
            detail = "SSD · TRIM 已开启"
    elif snapshot.media == "HDD":
        detail = "HDD · 建议定期碎片整理"
    elif snapshot.media == "混合":
        detail = "SSD+HDD"

    if snapshot.smart_ok is False:
        disk_score = 0.0
        detail = "SMART 告警"
        measured = True
    elif snapshot.smart_ok is True:
        detail += " · SMART 正常"
    factors.append(Factor("disk", "磁盘健康", 11, disk_score, detail, measured))

    # 9. 文件系统与 4K 对齐 7
    fs_score = 5.0
    fs_detail = snapshot.file_system or "未知"
    if snapshot.file_system.upper() == "NTFS":
        if snapshot.aligned is True:
            fs_score = 7.0
            fs_detail = "NTFS · 已 4K 对齐"
        elif snapshot.aligned is False:
            fs_score = 4.0
            fs_detail = "NTFS · 未 4K 对齐"
        else:
            fs_score = 6.0
            fs_detail = "NTFS"
    elif snapshot.file_system:
        fs_score = 2.0
        fs_detail = f"{snapshot.file_system}（非 NTFS）"
    factors.append(
        Factor(
            "fs",
            "文件系统与对齐",
            7,
            fs_score,
            fs_detail,
            measured=bool(snapshot.file_system),
        )
    )

    # 10. 更新 / 临时文件积压 5
    factors.append(
        Factor(
            "upd",
            "更新/临时文件积压",
            5,
            smaller_better(snapshot.update_backlog, 2 * GB, 10 * GB, 5),
            _size(snapshot.update_backlog) if snapshot.has_update else "未扫描",
            measured=snapshot.has_update,
        )
    )

    # 11. 可释放系统文件 5
    factors.append(
        Factor(
            "rel",
            "可释放系统文件",
            5,
            smaller_better(snapshot.releasable, 1 * GB, 20 * GB, 5),
            _size(snapshot.releasable),
            measured=True,
        )
    )

    measured_weight = sum(f.weight for f in factors if f.measured)
    gained = sum(f.score for f in factors if f.measured)
    score = round(gained / measured_weight * 100) if measured_weight else 0
    level, color = level_of(score)

    return HealthReport(
        score=score,
        level=level,
        level_color=color,
        factors=factors,
        advice=build_advice(snapshot, factors),
        measured_weight=measured_weight,
        total_weight=sum(f.weight for f in factors),
        snapshot=snapshot,
        scanned_at=time.time(),
    )


def level_of(score: int) -> tuple[str, str]:
    for threshold, label, color in LEVELS:
        if score >= threshold:
            return label, color
    return "危险", "red"


# 因子 → 能真正解决问题的页面。没有对应页面的项（容量、SMART、4K 对齐、
# 休眠文件）故意不给入口：按钮点进去什么也做不了，比没有按钮更糟。
FACTOR_PAGE = {
    "free": "bigfile",     # 空间不够 → 大文件页挑着删
    "junk": "clean",
    "dup": "dup",
    "social": "clean",     # 社交缓存是清理页里的一组规则
    "reg": "registry",
    "startup": "startup",
    "upd": "clean",
    # 休眠文件 / 旧系统文件现在有地方处理了：系统瘦身的空间回收
    "rel": "slim",
}


def build_advice(snapshot: HealthSnapshot, factors: list[Factor]) -> list[Advice]:
    out: list[Advice] = []

    def low(key: str) -> bool:
        factor = next((f for f in factors if f.key == key), None)
        return bool(factor and factor.measured and factor.ratio < 0.7)

    def add(key: str, text: str) -> None:
        out.append(Advice(text, FACTOR_PAGE.get(key, "")))

    if low("free"):
        if snapshot.partition_count == 1:
            add(
                "free",
                f"系统盘只剩 {_size(snapshot.system_free)}，本机只有一个分区，"
                "没有第二个盘可以迁 —— 到「磁盘空间」看看哪些能回收。",
            )
        else:
            add(
                "free",
                f"系统盘只剩 {_size(snapshot.system_free)}，建议清理大文件或把缓存目录迁到其他盘。",
            )
    if low("junk"):
        add("junk", f"垃圾文件约 {_size(snapshot.junk_bytes)}，建议执行一次垃圾清理。")
    if low("dup"):
        add("dup", f"重复文件占用 {_size(snapshot.dup_bytes)}，建议清理冗余副本。")
    if low("social"):
        add(
            "social",
            f"社交软件缓存达 {_size(snapshot.social_bytes)}，可清理图片/视频缓存。",
        )
    if low("reg"):
        add("reg", f"注册表冗余约 {snapshot.registry_count} 项，建议备份后清理。")
    if low("startup"):
        add(
            "startup",
            f"启动项有 {snapshot.startup_count} 个，建议禁用不必要的项以加快开机。",
        )
    if low("disk"):
        # 硬件告警：只能提醒备份，跳转没有任何意义
        add("disk", "磁盘健康异常，建议尽快备份重要数据并检查硬盘状态。")
    if low("upd"):
        add(
            "upd",
            f"更新/临时文件积压 {_size(snapshot.update_backlog)}，建议清理更新缓存。",
        )
    if low("rel"):
        add(
            "rel",
            f"存在可释放的系统文件 {_size(snapshot.releasable)}（休眠文件/旧系统），"
            "确认不需要休眠或回退后可以清理。",
        )

    # 未扫描的项必须单独说清楚。原来只在「out 为空」时给一句
    # 「各项指标健康」，可是 11 项里经常有 5 项是灰的，
    # 权重才覆盖 63/100，说健康反而误导。
    pending = [f for f in factors if not f.measured]
    if pending:
        names = "、".join(f.label for f in pending[:3])
        more = f" 等 {len(pending)} 项" if len(pending) > 3 else ""
        out.append(
            Advice(f"{names}{more}还没扫描，点「全面体检」补齐后才会纳入评分和建议。")
        )
    elif not out:
        out.append(Advice("各项指标健康，无需特别操作。"))
    return out


# ---------------------------------------------------------------- 采集

_PROBE_CACHE: tuple[float, dict] | None = None
_PROBE_TTL = 300.0


def _trim_enabled() -> bool | None:
    """TRIM 状态。

    注意：`DisableDeleteNotify = 0` 表示「禁用删除通知」这一项是关的，
    也就是 **TRIM 已开启**。中文输出会显示「已禁用」，直接读文字会判断反。
    """
    try:
        result = subprocess.run(
            ["fsutil", "behavior", "query", "DisableDeleteNotify"],
            capture_output=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None

    raw = result.stdout or b""
    for encoding in ("mbcs", "utf-8", "gbk"):
        try:
            text = raw.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        text = raw.decode("utf-8", "replace")

    for line in text.splitlines():
        if "ntfs" not in line.lower():
            continue
        if "=" not in line:
            continue
        try:
            return int(line.split("=")[1].strip().split()[0]) == 0
        except (IndexError, ValueError):
            continue
    return None


def _disk_probe() -> dict:
    global _PROBE_CACHE
    if _PROBE_CACHE and time.monotonic() - _PROBE_CACHE[0] < _PROBE_TTL:
        return _PROBE_CACHE[1]
    try:
        from . import diskhealth

        info = diskhealth.summary()
    except Exception:
        info = {"media": "Unknown", "smart_ok": None, "aligned": None}
    info["trim_enabled"] = _trim_enabled()
    _PROBE_CACHE = (time.monotonic(), info)
    return info


def _hiberfil_size() -> int:
    drive = os.environ.get("SystemDrive", "C:")
    try:
        return os.path.getsize(os.path.join(drive + "\\", "hiberfil.sys"))
    except OSError:
        return 0


def probe_system(should_stop=lambda: False) -> HealthSnapshot | None:
    """探测系统侧的数据（系统盘、磁盘健康、启动项、内存）。

    这一步要开物理磁盘句柄、读注册表，是最慢的部分。所以单独拆出来：
    体检页会缓存结果，只在垃圾/重复文件变化时重算分数，不重新探测。
    """
    import psutil

    snapshot = HealthSnapshot()
    drive = os.environ.get("SystemDrive", "C:") + "\\"

    try:
        usage = psutil.disk_usage(drive)
        snapshot.system_total = usage.total
        snapshot.system_free = usage.free
    except OSError:
        applog.failed("health.probe_gpu")
    if should_stop():
        return None

    try:
        snapshot.file_system = next(
            (
                part.fstype
                for part in psutil.disk_partitions(all=False)
                if part.mountpoint.rstrip("\\").lower() == drive.rstrip("\\").lower()
            ),
            "",
        )
    except Exception:
        applog.failed("health.probe_startup")
    if should_stop():
        return None

    probe = _disk_probe()
    snapshot.media = probe.get("media", "Unknown")
    snapshot.smart_ok = probe.get("smart_ok")
    snapshot.aligned = probe.get("aligned")
    snapshot.trim_enabled = probe.get("trim_enabled")
    snapshot.releasable = _hiberfil_size()
    if should_stop():
        return None

    try:
        from . import startup

        snapshot.startup_count = startup.count_enabled()
    except Exception:
        snapshot.startup_count = 0

    try:
        snapshot.memory_percent = psutil.virtual_memory().percent
    except Exception:
        applog.failed("health.probe_partitions")

    try:
        snapshot.partition_count = len(
            [p for p in psutil.disk_partitions(all=False) if p.fstype]
        )
    except Exception:
        snapshot.partition_count = 0

    return snapshot


def collect(
    junk_bytes: int | None = None,
    dup_bytes: int | None = None,
    update_backlog: int | None = None,
    social_bytes: int | None = None,
    registry_count: int | None = None,
    should_stop=lambda: False,
    snapshot: HealthSnapshot | None = None,
) -> HealthReport | None:
    """采集数据并评分。

    垃圾体积、重复文件体积由调用方（清理模块 / 查重模块）传入；
    没传就标记为未测，不会凭空按 0 分算。

    传入 `snapshot` 时跳过系统探测，只重算分数 —— 体检页在垃圾/重复文件
    更新后走这条路，避免每次都为了一份评分重新开物理磁盘句柄。

    `should_stop` 用来支持取消：磁盘探测和注册表读取不可中断，但每一步之间
    会检查一次，用户点了取消就不用等剩下的步骤。
    """
    if snapshot is None:
        snapshot = probe_system(should_stop)
        if snapshot is None:
            return None
    else:
        # 复制一份，别改到调用方缓存的那份
        snapshot = replace(snapshot)

    if junk_bytes is not None:
        snapshot.junk_bytes = junk_bytes
        snapshot.has_junk = True
    if dup_bytes is not None:
        snapshot.dup_bytes = dup_bytes
        snapshot.has_dup = True
    if update_backlog is not None:
        snapshot.update_backlog = update_backlog
        snapshot.has_update = True
    if social_bytes is not None:
        snapshot.social_bytes = social_bytes
        snapshot.has_social = True
    if registry_count is not None:
        snapshot.registry_count = registry_count
        snapshot.has_registry = True

    return compute(snapshot)
