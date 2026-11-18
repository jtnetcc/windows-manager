"""界面停顿监视：记录 UI 线程被占住多久、卡在哪一行。

为什么要常驻：用户反馈「启动后卡三分钟」，但日志里一条线索都没有 ——
卡顿不像异常，它不会留下任何痕迹。这里装一个心跳：主线程每 100ms 打一拍，
另一个线程盯着这拍子；多久没动就是界面被占住多久。

停顿超过 2 秒时，顺带把所有线程的调用栈追加到 `logs/stalls.log`，
下次再有人说卡，不用猜，直接看栈。

开销：一次时间戳赋值 + 每 0.5 秒一次比较，可以忽略。
"""

from __future__ import annotations

import faulthandler
import threading
import time

from PySide6.QtCore import QObject, QTimer

from core import applog


class StallWatch(QObject):
    def __init__(
        self,
        interval_ms: int = 100,
        slow_ms: int = 800,
        dump_ms: int = 2000,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._slow = slow_ms / 1000.0
        self._dump = dump_ms / 1000.0
        self._beat = time.monotonic()
        self._started_at = time.monotonic()
        self._reported = False
        self._running = True

        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick)

        self._monitor = threading.Thread(
            target=self._watch, daemon=True, name="stall-watch"
        )

    # -- 生命周期 --------------------------------------------------

    def start(self) -> None:
        self._beat = time.monotonic()
        self._started_at = self._beat
        self._timer.start()
        self._monitor.start()

    def stop(self) -> None:
        self._running = False
        self._timer.stop()

    # -- 主线程 ----------------------------------------------------

    def _tick(self) -> None:
        self._beat = time.monotonic()

    # -- 监视线程 --------------------------------------------------

    def _watch(self) -> None:
        while self._running:
            time.sleep(0.5)
            idle = time.monotonic() - self._beat
            if idle < self._slow:
                self._reported = False
                continue
            if self._reported:
                continue          # 同一次停顿只记一条
            self._reported = True

            at = self._beat - self._started_at
            applog.logger("ui").warning(
                "界面停顿 %.1f 秒（启动后 %.0f 秒）", idle, at
            )
            if idle >= self._dump:
                self._dump_stacks(idle, at)

    @staticmethod
    def _dump_stacks(idle: float, at: float) -> None:
        """把当时的调用栈追加到独立文件：卡住的那一行就在里面。"""
        try:
            path = applog.log_dir() / "stalls.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(
                    f"\n===== 停顿 {idle:.1f}s（启动后 {at:.0f}s）"
                    f" {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n"
                )
                faulthandler.dump_traceback(file=handle)
        except OSError:
            applog.silenced("ui.stallwatch.dump")
