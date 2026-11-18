"""把一次可能很慢的调用挪到后台线程，主线程用定时器收结果。

为什么不用 QThread + 信号：这个项目里踩过两次 —— 回调落地时页面对象已经被
换肤重建销毁，直接 RuntimeError 或静默丢回调。改成「线程只写一个变量，
主线程定时器来取」，跨线程只剩下一个赋值动作，没有 Qt 对象的生命周期问题。

用法：

    self._bg = BackgroundCall(self)
    self._bg.start(lambda: updater.check(), self._on_update_checked)

`on_done(value, error)`：成功时 error 为空串，失败/超时时 value 为 None。
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, QTimer

from core import applog


class BackgroundCall(QObject):
    """一次性后台调用。同一个对象可以复用，但同一时间只跑一个。"""

    def __init__(self, parent: QObject | None = None, interval_ms: int = 120):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._poll)

        self._thread: threading.Thread | None = None
        self._done = False
        self._value = None
        self._error = ""
        self._on_done = None
        self._deadline = 0.0

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def start(self, fn, on_done, timeout_ms: int = 20000) -> bool:
        """起一个后台调用。已有调用在跑时返回 False。"""
        if self._thread is not None:
            return False

        self._done = False
        self._value = None
        self._error = ""
        self._on_done = on_done
        self._deadline = time.monotonic() + timeout_ms / 1000.0

        def run() -> None:
            value, error = None, ""
            try:
                value = fn()
            except Exception as exc:  # noqa: BLE001 — 后台线程里不许往上抛
                error = f"{type(exc).__name__}: {exc}"
                applog.failed("ui.bg.BackgroundCall")
            # 先写数据再置标志：主线程看到 _done 时数据一定已经写好了
            self._value, self._error = value, error
            self._done = True

        self._thread = threading.Thread(target=run, daemon=True, name="bg-call")
        self._thread.start()
        self._timer.start()
        return True

    def cancel(self) -> None:
        """放弃等待。线程本身无法打断，但结果会被丢掉。"""
        self._timer.stop()
        self._thread = None
        self._on_done = None

    def _poll(self) -> None:
        if not self._done:
            if time.monotonic() < self._deadline:
                return
            # 超时：调用本身可能还卡在 DNS 里（urllib 的 timeout 管不到
            # getaddrinfo），但界面不能再等了，直接放弃这一轮
            self._finish(None, "超时")
            return
        self._finish(self._value, self._error)

    def _finish(self, value, error: str) -> None:
        callback = self._on_done
        self.cancel()
        if callback is not None:
            callback(value, error)
