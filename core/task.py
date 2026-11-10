"""全局任务中心：扫描任务统一排队、串行执行。

替换掉「每个页面自己起一个 QThread」的做法，解决三个问题：

1. **切走页面就断** —— 查重跑五分钟，用户切到别的页面，原来的线程还挂在
   那个页面上，回来发现进度没了。
2. **并发抢磁盘** —— 两个扫描同时跑，磁盘 IO 争用，两边都变慢。
   GrClean 的实践结论是：并发扫盘不是更快，是更慢。
3. **状态散落** —— 进度、取消、失败各写各的，没法统一展示。

任务函数签名：`fn(report, should_stop) -> result`
- `report(percent, text)` 上报进度
- `should_stop()` 返回 True 表示用户要取消，任务应在检查点尽快返回
"""

from __future__ import annotations

from collections import deque
from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal

TaskFn = Callable[[Callable[[int, str], None], Callable[[], bool]], object]


class _Worker(QThread):
    progressed = Signal(int, str)
    done = Signal(object, str)

    def __init__(self, fn: TaskFn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._stop = False

    def cancel(self) -> None:
        self._stop = True

    def run(self) -> None:
        result, error = None, ""
        try:
            result = self._fn(self.progressed.emit, lambda: self._stop)
        except Exception as exc:  # 任务炸了也要把控制权交回去
            # 异常必须单独带出去：只 emit(None) 的话，调用方会把它当成
            # 「用户取消」—— 页面显示「已取消」，实际是扫描崩了，
            # 用户点几次都得不到结果，日志里也只有终端一行。
            error = f"{type(exc).__name__}: {exc}"
            print(f"[task] 执行失败: {error}")
        self.done.emit(result, error)


class TaskCenter(QObject):
    started = Signal(str, str)          # task_id, 描述
    progress = Signal(str, int, str)    # task_id, 百分比, 文案
    finished = Signal(str, object)      # task_id, 结果
    cancelled = Signal(str)             # task_id
    failed = Signal(str, str, str)      # task_id, 描述, 错误信息
    queue_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue: deque[tuple[str, str, TaskFn]] = deque()
        self._worker: _Worker | None = None
        self._current_id = ""
        self._current_label = ""

    # ---------------------------------------------------------- 查询

    @property
    def current_id(self) -> str:
        return self._current_id

    @property
    def current_label(self) -> str:
        return self._current_label

    def is_busy(self, task_id: str | None = None) -> bool:
        if task_id is None:
            return self._worker is not None
        if self._worker is not None and self._current_id == task_id:
            return True
        return any(item[0] == task_id for item in self._queue)

    def pending(self) -> list[tuple[str, str]]:
        out = []
        if self._worker is not None:
            out.append((self._current_id, self._current_label))
        out.extend((task_id, label) for task_id, label, _fn in self._queue)
        return out

    # ---------------------------------------------------------- 调度

    def submit(self, task_id: str, label: str, fn: TaskFn) -> bool:
        """排队一个任务。同一任务已在排队/运行中则忽略，返回 False。"""
        if self.is_busy(task_id):
            return False
        self._queue.append((task_id, label, fn))
        self.queue_changed.emit()
        self._pump()
        return True

    def cancel(self, task_id: str) -> bool:
        """取消任务：运行中的置停止位，排队中的直接移出队列。"""
        if self._worker is not None and self._current_id == task_id:
            self._worker.cancel()
            return True

        for index, (queued_id, _label, _fn) in enumerate(self._queue):
            if queued_id == task_id:
                del self._queue[index]
                self.queue_changed.emit()
                self.cancelled.emit(task_id)
                return True
        return False

    def cancel_all(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
        self._queue.clear()
        self.queue_changed.emit()

    def shutdown(self, wait_ms: int = 2000) -> None:
        """退出前收尾，别让 QThread 在运行中被析构。"""
        self.cancel_all()
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(wait_ms)

    # ---------------------------------------------------------- 内部

    def _pump(self) -> None:
        if self._worker is not None or not self._queue:
            return

        task_id, label, fn = self._queue.popleft()
        self._current_id = task_id
        self._current_label = label

        worker = _Worker(fn, self)
        self._worker = worker
        worker.progressed.connect(
            lambda percent, text, tid=task_id: self.progress.emit(tid, percent, text)
        )
        worker.done.connect(
            lambda result, error, tid=task_id: self._on_done(tid, result, error)
        )
        worker.start()

        self.started.emit(task_id, label)
        self.queue_changed.emit()

    def _on_done(self, task_id: str, result, error: str) -> None:
        worker = self._worker
        label = self._current_label
        self._worker = None
        self._current_id = ""
        self._current_label = ""

        if worker is not None:
            worker.deleteLater()

        if error:
            # 先报失败原因，再发 cancelled 让页面把按钮和进度文案复位：
            # 页面只需要知道「这个任务没有结果」，具体原因由主窗口统一提示。
            self.failed.emit(task_id, label, error)
            self.cancelled.emit(task_id)
        elif result is None:
            self.cancelled.emit(task_id)
        else:
            self.finished.emit(task_id, result)

        self.queue_changed.emit()
        # 队列里还有就接着跑
        self._pump()
