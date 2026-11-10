"""计划任务页：单独看任务计划程序里的自启项。

启动项页已经能列出计划任务，但混在注册表和启动文件夹里，
看不出「下次什么时候跑」。这一页专门管它。
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import startup
from core.store import StartupState

from . import theme_dark as T
from .components import Card, label


class TasksPage(QWidget):
    COLUMNS = ("任务名", "下次运行", "状态")

    def __init__(self, store, tasks, parent=None):
        super().__init__(parent)
        self.store = store
        self.tasks = tasks
        self._entries: list[startup.StartupEntry] = []

        self.store.startup_changed.connect(self._render)
        self.tasks.finished.connect(self._on_task_finished)
        self.tasks.cancelled.connect(self._on_task_cancelled)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.lbl_stat = label(
            "点右侧「刷新」读取任务计划程序，约 1 秒", 12, T.TEXT_DIM
        )
        bar.addWidget(self.lbl_stat, 0)
        bar.addStretch(1)

        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setObjectName("Ghost")
        self.btn_refresh.setFixedHeight(30)
        self.btn_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_refresh.clicked.connect(self.start_scan)
        bar.addWidget(self.btn_refresh)

        self.btn_toggle = QPushButton("启用 / 禁用")
        self.btn_toggle.setObjectName("Primary")
        self.btn_toggle.setFixedHeight(30)
        self.btn_toggle.setCursor(Qt.PointingHandCursor)
        self.btn_toggle.clicked.connect(self._toggle)
        self.btn_toggle.setEnabled(False)
        bar.addWidget(self.btn_toggle)

        card = Card(padding=10, spacing=8)
        card.body().addLayout(bar)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.table.horizontalHeader()
        # 其余页都用 make_table，表头左对齐；这三页是手写的，
        # 不补这一行表头会居中，和单元格左对齐的正文错位
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.setColumnWidth(1, 170)
        self.table.setColumnWidth(2, 84)
        self.table.verticalHeader().setDefaultSectionSize(30)
        card.body().addWidget(self.table, 1)
        root.addWidget(card, 1)

        self.lbl_hint = label(
            "只列非系统任务（已排除 \\Microsoft\\ 下的内置项）。禁用后仍可随时启用。",
            11,
            T.TEXT_DIM,
        )
        root.addWidget(self.lbl_hint)

    # ---------------------------------------------------------- 读取

    def start_scan(self) -> None:
        # 用自己的 task_id：以前和启动项页共用 "startup"，两个页会互相
        # 判定为「忙」，在计划任务页点刷新毫无反应
        if self.tasks.is_busy("tasks"):
            self.lbl_stat.setText("已在读取中…")
            return
        self.btn_refresh.setEnabled(False)
        self.lbl_stat.setText("正在读取…")
        self.tasks.submit("tasks", "读取计划任务", self._make_task())

    def _make_task(self):
        def run(report, should_stop):
            report(20, "正在查询任务计划程序…")
            # 强制不走缓存：这一页要的是最新状态
            entries = startup.list_entries(include_tasks=True, use_cache=False)
            if should_stop():
                return None
            return StartupState(entries=entries, scanned_at=time.time())

        return run

    def _on_task_finished(self, task_id: str, _result) -> None:
        if task_id == "tasks":
            self.btn_refresh.setEnabled(True)

    def _on_task_cancelled(self, task_id: str) -> None:
        if task_id == "tasks":
            self.btn_refresh.setEnabled(True)
            self.lbl_stat.setText("已取消")

    def _render(self) -> None:
        self._entries = [e for e in self.store.startup.entries if e.source == "task"]
        self.btn_refresh.setEnabled(True)

        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(self._entries))
        for row, entry in enumerate(self._entries):
            cells = (
                entry.name,
                getattr(entry, "next_run", "") or "—",
                "已启用" if entry.enabled else "已禁用",
            )
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                if col == 2:
                    item.setForeground(
                        Qt.GlobalColor.green if entry.enabled else Qt.GlobalColor.gray
                    )
                self.table.setItem(row, col, item)
        self.table.setUpdatesEnabled(True)

        enabled = sum(1 for e in self._entries if e.enabled)
        if not self._entries:
            self.lbl_stat.setText("没有第三方计划任务")
        else:
            self.lbl_stat.setText(f"{len(self._entries)} 个任务 · {enabled} 个已启用")
        self.btn_toggle.setEnabled(bool(self._entries))

    # ---------------------------------------------------------- 操作

    def _toggle(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        targets = [self._entries[r] for r in rows if r < len(self._entries)]
        if not targets:
            QMessageBox.information(self, "未选择", "请先选中要启停的任务。")
            return

        ok = 0
        failed = 0
        for entry in targets:
            success, _msg = startup.set_enabled(entry.id, not entry.enabled)
            if success:
                ok += 1
            else:
                failed += 1

        message = f"已切换 {ok} 个任务"
        if failed:
            message += f"，{failed} 个失败（多半是权限不足）"
        self.lbl_hint.setText(message)
        self.start_scan()

    def shutdown(self) -> None:
        if self.tasks.is_busy("tasks"):
            self.tasks.cancel("tasks")
