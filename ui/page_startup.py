"""启动项页：列出三处来源的开机启动项，可启用/禁用。"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import startup
from core.store import StartupState

from . import theme_dark as T
from .components import Card, ToggleSwitch, font, label, make_table


class StartupPage(QWidget):
    def __init__(self, store, tasks, parent=None):
        super().__init__(parent)
        self.store = store
        self.tasks = tasks
        self._entries: list[startup.StartupEntry] = []
        self._loading = False

        self.store.startup_changed.connect(self._render_startup)
        self.tasks.finished.connect(self._on_task_finished)
        self.tasks.cancelled.connect(self._on_task_cancelled)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(9)

        card = Card(padding=12, spacing=8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(7)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索启动项…")
        self.search.setFixedHeight(28)
        self.search.setFixedWidth(200)
        self.search.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self.search)

        self.btn_refresh = QPushButton("重新扫描")
        self.btn_refresh.setObjectName("Ghost")
        self.btn_refresh.clicked.connect(lambda: self.start_scan(use_cache=False))
        toolbar.addWidget(self.btn_refresh)

        toolbar.addStretch(1)
        self.lbl_count = label("点右侧「刷新」读取注册表与启动文件夹，约 1 秒", 11, T.TEXT_DIM)
        toolbar.addWidget(self.lbl_count)
        card.body().addLayout(toolbar)

        self.table = make_table(
            ["名称", "来源", "状态", "命令"], elide=Qt.ElideRight
        )

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 96)
        self.table.setColumnWidth(2, 64)

        card.add(self.table, 1)
        root.addWidget(card, 1)

        hint = label(
            "禁用不会删除任何东西：注册表项会被改名加 .off 后缀，快捷方式同理，"
            "随时可以改回来。",
            10,
            T.TEXT_DIM,
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

    # ---------------------------------------------------------- 数据

    def start_scan(self, use_cache: bool = True) -> None:
        if self.tasks.is_busy("startup"):
            return
        self.btn_refresh.setEnabled(False)
        self.lbl_count.setText("正在扫描…")
        self.tasks.submit("startup", "读取启动项", self._make_task(use_cache))

    def _make_task(self, use_cache: bool):
        def run(report, should_stop):
            report(20, "正在读取注册表与启动文件夹…")
            entries = startup.list_entries(use_cache=use_cache)
            if should_stop():
                return None
            return StartupState(entries=entries, scanned_at=time.time())

        return run

    def _on_task_finished(self, task_id: str, _result) -> None:
        if task_id == "startup":
            self.btn_refresh.setEnabled(True)

    def _on_task_cancelled(self, task_id: str) -> None:
        if task_id != "startup":
            return
        self.btn_refresh.setEnabled(True)
        self.lbl_count.setText("已取消")

    def shutdown(self) -> None:
        if self.tasks.is_busy("startup"):
            self.tasks.cancel("startup")

    def _render_startup(self) -> None:
        self._entries = list(self.store.startup.entries)
        self.btn_refresh.setEnabled(True)
        self._apply_filter()

    def _apply_filter(self) -> None:
        keyword = self.search.text().strip().lower()
        rows = self._entries
        if keyword:
            rows = [
                e
                for e in rows
                if keyword in e.name.lower()
                or keyword in e.command.lower()
                or keyword in e.source_text
            ]

        self._loading = True
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(rows))

        for index, entry in enumerate(rows):
            name_item = QTableWidgetItem(entry.name)
            name_item.setData(Qt.UserRole, entry.id)
            name_item.setFont(font(12))
            name_item.setToolTip(entry.name)
            self.table.setItem(index, 0, name_item)

            source_item = QTableWidgetItem(entry.source_text)
            source_item.setFont(font(11))
            source_item.setForeground(QColor(T.TEXT_DIM))
            self.table.setItem(index, 1, source_item)

            switch = ToggleSwitch()
            switch.set_checked_silently(entry.enabled)
            switch.toggled.connect(
                lambda checked, eid=entry.id: self._on_toggle(eid, checked)
            )
            holder = QWidget()
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(0, 0, 0, 0)
            holder_layout.setAlignment(Qt.AlignCenter)
            holder_layout.addWidget(switch)
            self.table.setCellWidget(index, 2, holder)

            cmd_item = QTableWidgetItem(entry.command)
            cmd_item.setFont(font(11))
            cmd_item.setForeground(QColor(T.TEXT_SUB))
            cmd_item.setToolTip(entry.command)
            self.table.setItem(index, 3, cmd_item)

        self.table.setUpdatesEnabled(True)
        self._loading = False

        enabled = sum(1 for e in self._entries if e.enabled)
        suffix = f" / {len(self._entries)}" if keyword else ""
        self.lbl_count.setText(f"{len(rows)}{suffix} 项 · 已启用 {enabled}")

    # ---------------------------------------------------------- 交互

    def _on_toggle(self, entry_id: str, enabled: bool) -> None:
        if self._loading:
            return
        ok, error = startup.set_enabled(entry_id, enabled)
        if not ok:
            QMessageBox.warning(self, "操作失败", error or "无法修改该启动项")
            self._apply_filter()
            return
        for entry in self._entries:
            if entry.id == entry_id:
                entry.enabled = enabled
                break
        enabled_count = sum(1 for e in self._entries if e.enabled)
        self.lbl_count.setText(f"{len(self._entries)} 项 · 已启用 {enabled_count}")
