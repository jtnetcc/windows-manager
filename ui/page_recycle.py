"""回收站页：看内容、按体积排序、还原或清空。"""

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

from core import recycle
from core.metrics import fmt_bytes

from . import theme_dark as T
from .components import Card, label


class RecyclePage(QWidget):
    COLUMNS = ("名称", "原位置", "大小", "删除时间")

    def __init__(self, tasks, parent=None):
        super().__init__(parent)
        self.tasks = tasks
        self._items: list[recycle.Item] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.lbl_stat = label("点右侧「刷新」读取回收站，约 1 秒", 12, T.TEXT_DIM)
        bar.addWidget(self.lbl_stat, 0)
        bar.addStretch(1)

        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setObjectName("Ghost")
        self.btn_refresh.setFixedHeight(30)
        self.btn_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_refresh.clicked.connect(self.start_scan)
        bar.addWidget(self.btn_refresh)

        self.btn_restore = QPushButton("还原选中")
        self.btn_restore.setObjectName("Ghost")
        self.btn_restore.setFixedHeight(30)
        self.btn_restore.setCursor(Qt.PointingHandCursor)
        self.btn_restore.clicked.connect(self._restore)
        self.btn_restore.setEnabled(False)
        bar.addWidget(self.btn_restore)

        self.btn_empty = QPushButton("清空回收站")
        self.btn_empty.setObjectName("Primary")
        self.btn_empty.setFixedHeight(30)
        self.btn_empty.setCursor(Qt.PointingHandCursor)
        self.btn_empty.clicked.connect(self._empty)
        self.btn_empty.setEnabled(False)
        bar.addWidget(self.btn_empty)

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
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 240)
        self.table.setColumnWidth(2, 92)
        self.table.setColumnWidth(3, 132)
        self.table.verticalHeader().setDefaultSectionSize(30)
        card.body().addWidget(self.table, 1)
        root.addWidget(card, 1)

        self.lbl_hint = label(
            "按体积从大到小排列。还原会放回原位置，原位置已有同名文件时自动改名，不会覆盖。",
            11,
            T.TEXT_DIM,
        )
        root.addWidget(self.lbl_hint)

    # ---------------------------------------------------------- 读取

    def start_scan(self) -> None:
        if self.tasks.is_busy("recycle"):
            return
        self.lbl_stat.setText("正在读取…")
        self.btn_refresh.setEnabled(False)
        self.tasks.submit("recycle", "读取回收站", self._make_task())

    def _make_task(self):
        def run(report, should_stop):
            report(20, "正在解析回收站…")
            items = recycle.list_items()
            if should_stop():
                return None
            return items

        return run

    def on_loaded(self, items: list[recycle.Item]) -> None:
        """主窗口把任务结果转发过来（回收站数据不进 store，用完即弃）。"""
        self._items = list(items)
        self.btn_refresh.setEnabled(True)
        self.btn_refresh.setText("刷新")
        self._fill()

    def _fill(self) -> None:
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(self._items))
        for row, item in enumerate(self._items):
            stamp = (
                time.strftime("%Y-%m-%d %H:%M", time.localtime(item.deleted_at))
                if item.deleted_at
                else "—"
            )
            for col, text in enumerate(
                (item.name, item.origin, fmt_bytes(item.size), stamp)
            ):
                cell = QTableWidgetItem(text)
                cell.setToolTip(text)
                if col == 2:
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, col, cell)
        self.table.setUpdatesEnabled(True)

        total = sum(i.size for i in self._items)
        if not self._items:
            self.lbl_stat.setText("回收站是空的")
        else:
            self.lbl_stat.setText(f"{len(self._items)} 项 · {fmt_bytes(total)}")
        self.btn_restore.setEnabled(bool(self._items))
        self.btn_empty.setEnabled(bool(self._items))

    # ---------------------------------------------------------- 操作

    def _selected(self) -> list[recycle.Item]:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        return [self._items[r] for r in rows if r < len(self._items)]

    def _restore(self) -> None:
        targets = self._selected()
        if not targets:
            QMessageBox.information(self, "未选择", "请先选中要还原的项目。")
            return
        ok, failed = recycle.restore(targets)
        message = f"已还原 {ok} 项"
        if failed:
            message += f"，{failed} 项失败（可能被占用或原路径无权限）"
        self.lbl_hint.setText(message)
        self.start_scan()

    def _empty(self) -> None:
        if not self._items:
            return
        total = sum(i.size for i in self._items)
        box = QMessageBox(self)
        box.setWindowTitle("确认清空")
        box.setText(
            f"将永久删除回收站里的 {len(self._items)} 项，共 {fmt_bytes(total)}。\n\n"
            "此操作不可撤销。"
        )
        box.setIcon(QMessageBox.Warning)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        if box.exec() != QMessageBox.Yes:
            return

        if recycle.empty():
            self.lbl_hint.setText(f"已清空，释放 {fmt_bytes(total)}")
        else:
            self.lbl_hint.setText("没能清空，可能有文件正被其他程序占用")
        self.start_scan()

    def shutdown(self) -> None:
        self.tasks.cancel_many("recycle")
