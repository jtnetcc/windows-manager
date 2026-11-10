"""进程页：搜索、排序、结束进程。"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMenu,
    QStyledItemDelegate,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import optimizer, winmem

from . import theme_dark as T
from .components import Card, ComboBox, copy_text, font, label, make_table, reveal_file


class ShareDelegate(QStyledItemDelegate):
    """占比列：百分比 + 细条。"""

    def paint(self, painter: QPainter, option, index) -> None:
        ratio = index.data(Qt.UserRole) or 0.0
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = option.rect.adjusted(4, 0, -10, 0)
        color = QColor(T.load_color(min(100.0, ratio * 4)))

        text_rect = QRectF(rect.left(), rect.top() + 3, rect.width(), 14)
        painter.setFont(font(11))
        painter.setPen(QColor(T.TEXT_SUB))
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, f"{ratio:.1f}%")

        bar_y = rect.center().y() + 4
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(T.BORDER_SOFT))
        painter.drawRoundedRect(QRectF(rect.left(), bar_y, rect.width(), 3), 1.5, 1.5)
        width = rect.width() * min(1.0, ratio / 100.0)
        if width > 1.0:
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(rect.left(), bar_y, width, 3), 1.5, 1.5)
        painter.restore()


class ProcessPage(QWidget):
    kill_requested = Signal(int, str)
    kill_tree_requested = Signal(int, str)
    whitelist_requested = Signal(str)

    # 表头列 -> 排序下拉框的索引
    SORT_BY_COLUMN = {0: 2, 2: 1, 3: 0, 4: 0}

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(9)

        card = Card(padding=12, spacing=8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(7)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索进程名或 PID…")
        self.search.setFixedHeight(28)
        self.search.setFixedWidth(220)
        self.search.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self.search)

        self.sort_box = ComboBox()
        self.sort_box.addItems(["按内存排序", "按 CPU 排序", "按名称排序"])
        self.sort_box.setFixedHeight(28)
        self.sort_box.setMinimumWidth(122)
        self.sort_box.currentIndexChanged.connect(self._apply_filter)
        toolbar.addWidget(self.sort_box)
        toolbar.addStretch(1)

        self.lbl_count = label("", 11, T.TEXT_DIM)
        toolbar.addWidget(self.lbl_count)
        card.body().addLayout(toolbar)

        self.table = make_table(
            ["进程", "PID", "CPU", "内存", "占比"],
            row_height=28,
            elide=Qt.ElideRight,
            menu=True,
        )
        self.table.customContextMenuRequested.connect(self._show_menu)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, 5):
            header.setSectionResizeMode(col, QHeaderView.Fixed)
        self.table.setColumnWidth(1, 74)
        self.table.setColumnWidth(2, 76)
        self.table.setColumnWidth(3, 96)
        self.table.setColumnWidth(4, 108)
        self.table.setItemDelegateForColumn(4, ShareDelegate(self.table))
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._on_header_clicked)

        card.add(self.table, 1)
        root.addWidget(card, 1)

        self._rows: list[dict] = []
        self._total = 0
        self._leaks: set[int] = set()

    # ---------------------------------------------------------- 数据

    def update_processes(self, rows: list[dict], total: int, leaks: set[int]) -> None:
        self._rows = rows
        self._total = total
        self._leaks = leaks
        self._apply_filter()

    def _apply_filter(self) -> None:
        keyword = self.search.text().strip().lower()
        rows = self._rows
        if keyword:
            rows = [
                r for r in rows
                if keyword in r["name"].lower() or keyword in str(r["pid"])
            ]

        mode = self.sort_box.currentIndex()
        if mode == 1:
            rows = sorted(rows, key=lambda r: r.get("cpu", 0), reverse=True)
        elif mode == 2:
            rows = sorted(rows, key=lambda r: r["name"].lower())

        self.table.setUpdatesEnabled(False)
        scroll = self.table.verticalScrollBar().value()
        self.table.setRowCount(len(rows))

        for index, row in enumerate(rows):
            name_item = QTableWidgetItem(row["name"])
            name_item.setData(Qt.UserRole, row["pid"])
            name_item.setFont(font(12))
            if row["pid"] in self._leaks:
                name_item.setForeground(QColor(T.AMBER))
                name_item.setToolTip("该进程内存持续增长，疑似泄漏")
            self.table.setItem(index, 0, name_item)

            pid_item = QTableWidgetItem(str(row["pid"]))
            pid_item.setFont(font(11))
            pid_item.setForeground(QColor(T.TEXT_DIM))
            self.table.setItem(index, 1, pid_item)

            cpu = row.get("cpu", 0.0)
            cpu_item = QTableWidgetItem(f"{cpu:.1f}%")
            cpu_item.setFont(font(11))
            cpu_item.setForeground(QColor(T.AMBER if cpu >= 25 else T.TEXT_SUB))
            self.table.setItem(index, 2, cpu_item)

            mem_item = QTableWidgetItem(winmem.fmt_size(row["memory"]))
            mem_item.setFont(font(12))
            self.table.setItem(index, 3, mem_item)

            share_item = QTableWidgetItem("")
            share = (row["memory"] / self._total * 100.0) if self._total else 0.0
            share_item.setData(Qt.UserRole, share)
            self.table.setItem(index, 4, share_item)

        self.table.verticalScrollBar().setValue(scroll)
        self.table.setUpdatesEnabled(True)

        suffix = f" / {len(self._rows)}" if keyword else ""
        self.lbl_count.setText(f"{len(rows)}{suffix} 个进程")

    # ---------------------------------------------------------- 交互

    def _on_header_clicked(self, column: int) -> None:
        """点表头也能排序，跟下拉框保持同步。"""
        index = self.SORT_BY_COLUMN.get(column)
        if index is not None:
            self.sort_box.setCurrentIndex(index)

    def _row_pid(self, row: int) -> int | None:
        item = self.table.item(row, 0)
        return item.data(Qt.UserRole) if item else None

    def _show_menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        pid = self._row_pid(row)
        if pid is None:
            return
        name = self.table.item(row, 0).text()
        # exe 路径按需查询：每次采样都为所有进程查一遍会慢 30 倍
        exe = optimizer.process_exe(pid)

        menu = QMenu(self)
        menu.addAction(f"结束 {name}").triggered.connect(
            lambda: self.kill_requested.emit(pid, name)
        )
        menu.addAction("结束进程树…").triggered.connect(
            lambda: self.kill_tree_requested.emit(pid, name)
        )
        menu.addAction("加入保护名单").triggered.connect(
            lambda: self.whitelist_requested.emit(name)
        )
        menu.addSeparator()
        open_action = menu.addAction("打开文件位置")
        open_action.setEnabled(bool(exe))
        open_action.triggered.connect(lambda: reveal_file(exe))
        menu.addAction("复制名称").triggered.connect(lambda: copy_text(name))
        menu.addAction("复制 PID").triggered.connect(lambda: copy_text(str(pid)))
        menu.exec(self.table.viewport().mapToGlobal(pos))
