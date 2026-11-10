"""告警引擎：CPU 持续高负载 / 内存压力 / 磁盘不足。

三条设计原则（都是踩过坑之后总结的）：

1. **每条规则独立冷却**。CPU 告警不能把磁盘告警压掉，否则用户永远只收到一条。
2. **CPU 必须"持续"超阈值**。编译一下、解压一下就弹窗会让人把告警关掉，
   所以要求连续 N 秒都在阈值之上才算。
3. **冷却期内不重复提示**，但状态仍然可以在历史里查到。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

GB = 1024 ** 3

LEVEL_WARN = "warn"
LEVEL_CRIT = "crit"


@dataclass(frozen=True)
class Alert:
    key: str
    level: str
    title: str
    message: str
    value: float
    timestamp: float


@dataclass
class AlertConfig:
    """告警阈值。由 Settings 派生，改动会立刻生效。"""

    enabled: bool = True
    sound: bool = True
    cpu_percent: int = 90
    cpu_seconds: int = 60
    memory_percent: int = 90
    disk_free_gb: float = 5.0

    @classmethod
    def from_settings(cls, s) -> "AlertConfig":
        return cls(
            enabled=getattr(s, "alerts_enabled", True),
            sound=getattr(s, "alert_sound", True),
            cpu_percent=getattr(s, "alert_cpu", 90),
            cpu_seconds=getattr(s, "alert_cpu_seconds", 60),
            memory_percent=getattr(s, "alert_memory", 90),
            disk_free_gb=getattr(s, "alert_disk_free_gb", 5.0),
        )


# 各规则冷却时间（秒）。磁盘几乎不会几分钟内变满，冷却给长一点。
COOLDOWN = {
    "cpu": 30 * 60.0,
    "memory": 15 * 60.0,
    "disk": 6 * 3600.0,
}


class AlertEngine:
    """吃快照、吐新告警。纯逻辑，不碰 UI，方便单独测。"""

    def __init__(self, config: AlertConfig | None = None) -> None:
        self.config = config or AlertConfig()
        self.history: deque[Alert] = deque(maxlen=60)
        self._last_fired: dict[str, float] = {}
        self._cpu_since: float | None = None
        self._mem_since: float | None = None
        self._disk_since: float | None = None
        self._now = 0.0
        self._suppressed = 0

    # ---------------------------------------------------------- 配置

    def configure(self, config: AlertConfig) -> None:
        self.config = config
        if not config.enabled:
            self.reset_timers()

    def reset_timers(self) -> None:
        self._cpu_since = None
        self._mem_since = None
        self._disk_since = None

    # ---------------------------------------------------------- 主入口

    def feed(self, snap) -> list[Alert]:
        """传入 MetricsSnapshot，返回本轮新触发的告警（可能为空）。"""
        if not self.config.enabled:
            self.reset_timers()
            return []

        now = snap.timestamp or time.time()
        self._now = now
        fired: list[Alert] = []
        for rule in (self._check_cpu, self._check_memory, self._check_disk):
            alert = rule(snap, now)
            if alert is not None:
                fired.append(alert)
                self.history.appendleft(alert)
        return fired

    # ---------------------------------------------------------- 规则

    def _check_cpu(self, snap, now: float) -> Alert | None:
        cfg = self.config
        value = snap.cpu.total

        if value < cfg.cpu_percent:
            self._cpu_since = None
            return None

        if self._cpu_since is None:
            self._cpu_since = now
        held = now - self._cpu_since
        if held < cfg.cpu_seconds:
            return None
        if not self._ready("cpu", now):
            return None

        self._mark("cpu", now)
        crit = value >= min(99.0, cfg.cpu_percent + 8)
        cores = snap.cpu.logical or 1
        hot = sum(1 for c in snap.cpu.per_core if c >= cfg.cpu_percent)
        return Alert(
            key="cpu",
            level=LEVEL_CRIT if crit else LEVEL_WARN,
            title="CPU 持续高负载",
            message=(
                f"已持续 {self._duration_text(held)} 高于 {cfg.cpu_percent}%，"
                f"当前 {value:.0f}%（{hot}/{cores} 个核心超过阈值）"
            ),
            value=value,
            timestamp=now,
        )

    def _check_memory(self, snap, now: float) -> Alert | None:
        cfg = self.config
        mem = snap.memory
        value = mem.percent

        if value < cfg.memory_percent:
            self._mem_since = None
            return None

        if self._mem_since is None:
            self._mem_since = now
        held = now - self._mem_since
        # 内存压力不需要等太久，10 秒足够排除瞬时抖动
        if held < 10.0:
            return None
        if not self._ready("memory", now):
            return None

        self._mark("memory", now)
        crit = value >= min(99.0, cfg.memory_percent + 5)
        from .winmem import fmt_size

        return Alert(
            key="memory",
            level=LEVEL_CRIT if crit else LEVEL_WARN,
            title="内存占用偏高",
            message=(
                f"已用 {value:.0f}%（{fmt_size(mem.used)} / {fmt_size(mem.total)}），"
                f"可用仅 {fmt_size(mem.avail)}"
            ),
            value=value,
            timestamp=now,
        )

    def _check_disk(self, snap, now: float) -> Alert | None:
        cfg = self.config
        disk = snap.disk
        if disk.total <= 0:
            return None

        threshold = int(cfg.disk_free_gb * GB)
        if disk.free > threshold:
            self._disk_since = None
            return None

        if self._disk_since is None:
            self._disk_since = now
        held = now - self._disk_since
        if held < 10.0:
            return None
        if not self._ready("disk", now):
            return None

        self._mark("disk", now)
        from .winmem import fmt_size

        crit = disk.free < threshold / 2
        return Alert(
            key="disk",
            level=LEVEL_CRIT if crit else LEVEL_WARN,
            title="磁盘空间不足",
            message=(
                f"{disk.mount or '系统盘'} 剩余 {fmt_size(disk.free)}"
                f"（{100 - disk.percent:.0f}% 空闲），低于 {cfg.disk_free_gb:.0f} GB"
            ),
            value=disk.free / GB,
            timestamp=now,
        )

    # ---------------------------------------------------------- 冷却

    def _ready(self, key: str, now: float) -> bool:
        # 不能用 0.0 当"从未触发"的哨兵：时间戳本身可能很小，
        # 会被误判成"还在冷却中"。缺失即视为可触发。
        last = self._last_fired.get(key)
        if last is None or now - last >= COOLDOWN.get(key, 1800.0):
            return True
        self._suppressed += 1
        return False

    def _mark(self, key: str, now: float) -> None:
        self._last_fired[key] = now

    @staticmethod
    def _duration_text(seconds: float) -> str:
        if seconds >= 120:
            return f"{seconds / 60:.0f} 分钟"
        return f"{seconds:.0f} 秒"

    # ---------------------------------------------------------- 查询

    def cooldown_left(self, key: str) -> float:
        last = self._last_fired.get(key)
        if last is None:
            return 0.0
        return max(0.0, COOLDOWN.get(key, 0.0) - (self._now - last))

    def status_lines(self) -> list[tuple[str, str]]:
        """给设置页显示当前冷却状态。"""
        names = {"cpu": "CPU", "memory": "内存", "disk": "磁盘"}
        rows = []
        for key, name in names.items():
            left = self.cooldown_left(key)
            rows.append((name, "可触发" if left <= 0 else f"冷却 {left / 60:.0f} 分钟"))
        return rows


def play_alert_sound(level: str = LEVEL_WARN) -> None:
    """告警提示音。

    之前用的是 `MessageBeep`，它播的是「系统事件声音」——而 Win11 默认把
    「关键停止」「警告」这两个事件设成了「无」，所以用户听到的是静音。
    这不是代码 bug，是设计上依赖了系统声音方案。

    改成直接合成音调（`winsound.Beep`），绕开声音方案，只要没静音就一定响：

    - 警告：两声短促的中音
    - 严重：三声下行，更急促

    `Beep` 在 Vista 之后走的是默认音频设备，不是主板蜂鸣器。
    """
    try:
        import winsound
    except Exception:
        return

    pattern = (
        [(988, 110), (784, 110), (988, 160)]
        if level == LEVEL_CRIT
        else [(880, 120), (660, 160)]
    )

    try:
        for freq, duration in pattern:
            winsound.Beep(freq, duration)
    except Exception:
        # 极少数环境没有可用的音频设备，退回系统提示音
        try:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass
