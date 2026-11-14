"""启动项页：注册表 / 启动文件夹 / 计划任务三处来源，一个表管完。

这一页原来被拆成两页（「开机启动」和「计划任务」），但两页读的是
`startup.list_entries()` 的同一份结果、写的是 `store.startup` 的同一个字段：
谁扫一次就会把对方的列表覆盖掉，而两边 task_id 还不一样，于是各自判定
「对方在忙」，点刷新没反应。

现在合成一页：一次扫描拿全三处来源，顶部用分段筛选。计划任务多出来的
「下次运行」单独占一列，其它来源显示破折号。
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
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
from .components import Card, Segmented, ToggleSwitch, font, label, make_table

# 分段就是「来源」的筛选器，和表格里的「来源」列一一对应
SEGMENTS = [
    ("all", "全部"),
    ("registry", "注册表"),
    ("folder", "启动文件夹"),
    ("task", "计划任务"),
]

SEGMENT_SUBS = {
    "all": "三处来源合并，已启用的排在前面",
    "registry": "HKCU / HKLM 的 Run 键",
    "folder": "启动文件夹里的快捷方式",
    "task": "任务计划程序里的第三方自启项，含下次运行时间",
}


class StartupPage(QWidget):
    segment_changed = Signal(str, str)

    def __init__(self, store, tasks, parent=None):
        super().__init__(parent)
        self.store = store
        self.tasks = tasks
        self._entries: list[startup.StartupEntry] = []
        self._loading = False
        self._key = SEGMENTS[0][0]

        self.store.startup_changed.connect(self._render_startup)
        self.tasks.finished.connect(self._on_task_finished)
        self.tasks.cancelled.connect(self._on_task_cancelled)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(7)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self.seg = Segmented(SEGMENTS)
        self.seg.changed.connect(self._on_segment)
        head.addWidget(self.seg)
        head.addStretch(1)
        root.addLayout(head)

        card = Card(padding=12, spacing=8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(7)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索名称或命令…")
        self.search.setFixedHeight(28)
        self.search.setFixedWidth(200)
        self.search.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self.search)

        self.btn_refresh = QPushButton("重新扫描")
        self.btn_refresh.setObjectName("Ghost")
        self.btn_refresh.clicked.connect(lambda: self.start_scan(use_cache=False))
        toolbar.addWidget(self.btn_refresh)

        toolbar.addStretch(1)
        self.lbl_count = label("正在读取注册表、启动文件夹与计划任务…", 11, T.TEXT_DIM)
        toolbar.addWidget(self.lbl_count)
        card.body().addLayout(toolbar)

        self.table = make_table(
            ["名称", "来源", "下次运行", "状态", "命令"], elide=Qt.ElideRight
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 88)
        self.table.setColumnWidth(2, 138)
        self.table.setColumnWidth(3, 64)

        card.add(self.table, 1)
        root.addWidget(card, 1)

        hint = label(
            "禁用不会删除任何东西：注册表项改名加 .off 后缀，快捷方式同理，"
            "计划任务改成禁用状态，随时可以改回来。",
            10,
            T.TEXT_DIM,
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

    # ---------------------------------------------------------- 分段

    def _on_segment(self, key: str) -> None:
        self._key = key
        self._apply_filter()
        self.segment_changed.emit(key, SEGMENT_SUBS.get(key, ""))

    def set_segment(self, key: str) -> None:
        if key not in dict(SEGMENTS):
            return
        self.seg.set_current(key, emit=False)
        self._on_segment(key)

    def current_segment(self) -> str:
        return self._key

    def subtitle(self) -> str:
        return SEGMENT_SUBS.get(self._key, "")

    # ---------------------------------------------------------- 数据

    def start_scan(self, use_cache: bool = True) -> None:
        if self.tasks.is_busy("startup"):
            return
        self.btn_refresh.setEnabled(False)
        self.lbl_count.setText("正在扫描…")
        self.tasks.submit("startup", "读取启动项", self._make_task(use_cache))

    def _make_task(self, use_cache: bool):
        def run(report, should_stop):
            report(20, "正在读取注册表、启动文件夹与计划任务…")
            entries = startup.list_entries(include_tasks=True, use_cache=use_cache)
            if should_stop():
                return None
            return StartupState(entries=entries, scanned_at=time.time())

        return run

    def ensure_scan(self) -> None:
        """首次打开时扫一次；已经有结果就不重复扫。"""
        if self.store.startup.ready:
            self._render_startup()
            return
        self.start_scan()

    def _on_task_finished(self, task_id: str, _result) -> None:
        if task_id == "startup":
            self.btn_refresh.setEnabled(True)

    def _on_task_cancelled(self, task_id: str) -> None:
        if task_id != "startup":
            return
        self.btn_refresh.setEnabled(True)
        self.lbl_count.setText("已取消")

    def shutdown(self) -> None:
        self.tasks.cancel_many("startup")

    def _visible_entries(self) -> list[startup.StartupEntry]:
        rows = self._entries
        if self._key != "all":
            rows = [e for e in rows if e.source == self._key]
        keyword = self.search.text().strip().lower()
        if keyword:
            rows = [
                e
                for e in rows
                if keyword in e.name.lower()
                or keyword in e.command.lower()
                or keyword in e.source_text
            ]
        return rows

    def _render_startup(self) -> None:
        self._entries = list(self.store.startup.entries)
        self.btn_refresh.setEnabled(True)
        self._apply_filter()

    def _apply_filter(self) -> None:
        rows = self._visible_entries()

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

            # 下次运行：只有计划任务有，其它来源留破折号，不编造内容
            next_run = getattr(entry, "next_run", "") or ""
            next_item = QTableWidgetItem(next_run or "—")
            next_item.setFont(font(11))
            next_item.setForeground(QColor(T.TEXT_SUB if next_run else T.TEXT_DIM))
            if next_run:
                next_item.setToolTip(f"下次运行：{next_run}")
            self.table.setItem(index, 2, next_item)

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
            self.table.setCellWidget(index, 3, holder)

            cmd_item = QTableWidgetItem(entry.command)
            cmd_item.setFont(font(11))
            cmd_item.setForeground(QColor(T.TEXT_SUB))
            cmd_item.setToolTip(entry.command)
            self.table.setItem(index, 4, cmd_item)

        self.table.setUpdatesEnabled(True)
        self._loading = False

        enabled = sum(1 for e in rows if e.enabled)
        if not rows:
            self.lbl_count.setText("没有匹配的启动项")
        else:
            self.lbl_count.setText(f"{len(rows)} 项 · 已启用 {enabled}")

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
        self._apply_filter()
