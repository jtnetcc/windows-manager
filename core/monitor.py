"""后台采样线程：内存快照、进程列表、内存泄漏检测。"""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import QThread, Signal

from . import optimizer, winmem
from .metrics import MetricsCollector
from . import applog

LEAK_WINDOW = 12          # 观察多少个采样点
LEAK_MIN_GROWTH = 60 * winmem.MB
LEAK_MIN_MEMORY = 100 * winmem.MB


class MetricsSampler(QThread):
    """按固定间隔采集 CPU / 内存 / 网络 / 磁盘，产出完整快照。"""

    sampled = Signal(object)

    def __init__(self, interval_ms: int = 1000, parent=None):
        super().__init__(parent)
        self._interval = max(400, interval_ms)
        self._running = True
        self._collector = MetricsCollector()

    def run(self) -> None:
        while self._running:
            try:
                self.sampled.emit(self._collector.sample())
            except Exception:
                # 采样循环抛一次就少一帧；持续抛等于监控静默停摆，必须留 WARNING
                applog.failed("monitor.MetricsSampler.run")
            self.msleep(self._interval)

    def set_interval(self, interval_ms: int) -> None:
        self._interval = max(400, interval_ms)

    def stop(self) -> None:
        self._running = False
        self.wait(2500)


class LeakTracker:
    """用「持续单调增长」判断疑似内存泄漏。"""

    def __init__(self) -> None:
        self._history: dict[int, deque[int]] = {}

    def update(self, rows: list[dict]) -> set[int]:
        alive = {r["pid"] for r in rows}
        for pid in list(self._history):
            if pid not in alive:
                del self._history[pid]

        leaks: set[int] = set()
        for row in rows:
            pid = row["pid"]
            series = self._history.setdefault(pid, deque(maxlen=LEAK_WINDOW))
            series.append(row["memory"])
            if len(series) < LEAK_WINDOW:
                continue
            values = list(series)
            growth = values[-1] - values[0]
            if growth < LEAK_MIN_GROWTH or values[-1] < LEAK_MIN_MEMORY:
                continue
            # 允许轻微回落，只要整体趋势向上
            drops = sum(1 for a, b in zip(values, values[1:]) if b < a - 4 * winmem.MB)
            if drops <= 1:
                leaks.add(pid)
        return leaks


class ProcessSampler(QThread):
    """定期读取进程列表，同时做泄漏分析。"""

    sampled = Signal(list, object)  # (rows, leak_pids)

    def __init__(self, interval_ms: int = 3000, parent=None):
        super().__init__(parent)
        self._interval = max(1000, interval_ms)
        self._running = True
        self._paused = False
        self._tracker = LeakTracker()

    def run(self) -> None:
        while self._running:
            if not self._paused:
                try:
                    rows = optimizer.process_list(limit=120)
                    leaks = self._tracker.update(rows)
                    self.sampled.emit(rows, leaks)
                except Exception:
                    applog.failed("monitor.ProcessSampler.run")
            self.msleep(self._interval)

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def stop(self) -> None:
        self._running = False
        self.wait(2500)
