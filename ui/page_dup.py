"""重复文件页：扫描 → 分组列表 → 每组保留一份，其余送回收站。"""

from __future__ import annotations

import os

import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import dup, trash, winpath
from core.store import DupState
from core.metrics import fmt_bytes

from . import theme_dark as T
from .components import (
    Card,
    CheckBox,
    ComboBox,
    copy_text,
    font,
    label,
    make_table,
    reveal_file,
)

SIZES = [(1, "≥ 1 MB"), (10, "≥ 10 MB"), (50, "≥ 50 MB"), (200, "≥ 200 MB")]


class DupPage(QWidget):
    def __init__(self, store, tasks, parent=None):
        super().__init__(parent)
        self.store = store
        self.tasks = tasks
        self._groups: list[dup.DupGroup] = []
        self._roots = winpath.home_roots()

        self.store.dup_changed.connect(self._render_dup)
        self.tasks.progress.connect(self._on_task_progress)
        self.tasks.finished.connect(self._on_task_finished)
        self.tasks.cancelled.connect(self._on_task_cancelled)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(9)

        card = Card(padding=12, spacing=8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(7)

        self.btn_dir = QPushButton("选择目录…")
        self.btn_dir.setObjectName("Ghost")
        self.btn_dir.clicked.connect(self._pick_dir)
        toolbar.addWidget(self.btn_dir)

        self.lbl_root = label(self._short_root(), 10, T.TEXT_DIM)
        toolbar.addWidget(self.lbl_root)

        self.size_box = ComboBox()
        self.size_box.addItems([text for _, text in SIZES])
        self.size_box.setCurrentIndex(1)
        self.size_box.setFixedHeight(28)
        self.size_box.setFixedWidth(110)
        toolbar.addWidget(self.size_box)

        self.btn_scan = QPushButton("开始扫描")
        self.btn_scan.setObjectName("Primary")
        self.btn_scan.setFixedHeight(28)
        self.btn_scan.clicked.connect(self.start_scan)
        toolbar.addWidget(self.btn_scan)

        self.btn_stop = QPushButton("停止")
        self.btn_stop.setObjectName("Ghost")
        self.btn_stop.clicked.connect(self._stop_scan)
        self.btn_stop.hide()
        toolbar.addWidget(self.btn_stop)

        toolbar.addStretch(1)
        self.lbl_stat = label("点右侧「开始扫描」，需遍历目录并比对内容，通常 1–5 分钟", 11, T.TEXT_DIM)
        toolbar.addWidget(self.lbl_stat)
        card.body().addLayout(toolbar)

        self.table = make_table(
            ["", "文件名", "份数", "单份大小", "可释放"], menu=True
        )
        self.table.customContextMenuRequested.connect(self._show_menu)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for col in (2, 3, 4):
            header.setSectionResizeMode(col, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 34)
        self.table.setColumnWidth(2, 62)
        self.table.setColumnWidth(3, 92)
        self.table.setColumnWidth(4, 92)

        card.add(self.table, 1)
        root.addWidget(card, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.btn_all = QPushButton("全选")
        self.btn_all.setObjectName("Ghost")
        self.btn_all.clicked.connect(self._toggle_all)
        bottom.addWidget(self.btn_all)

        self.btn_clean = QPushButton("清理选中（送回收站）")
        self.btn_clean.setObjectName("Primary")
        self.btn_clean.setFixedHeight(30)
        self.btn_clean.clicked.connect(self._clean)
        bottom.addWidget(self.btn_clean)

        bottom.addStretch(1)
        self.lbl_total = label("", 11, T.TEXT_DIM)
        bottom.addWidget(self.lbl_total)
        root.addLayout(bottom)

        hint = label(
            "每组保留最早修改的那一份，其余送进回收站，随时可以还原。"
            "只比对体积 ≥ 阈值的文件。",
            10,
            T.TEXT_DIM,
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

    # ---------------------------------------------------------- 扫描

    def _short_root(self) -> str:
        if len(self._roots) == 1:
            path = self._roots[0]
            home = os.path.expanduser("~")
            if path.lower().startswith(home.lower()):
                return "~" + path[len(home):]
            return path
        return f"{len(self._roots)} 个目录"

    def _pick_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择要扫描的目录", self._roots[0])
        if path:
            self._roots = [path]
            self.lbl_root.setText(self._short_root())
            self.lbl_root.setToolTip(path)

    def start_scan(self) -> None:
        if self.tasks.is_busy("dup"):
            return
        self._groups.clear()
        self.table.setRowCount(0)
        self.lbl_total.setText("")
        self.lbl_stat.setText("正在扫描…")
        self.btn_scan.setEnabled(False)
        self.btn_stop.show()

        min_size = SIZES[self.size_box.currentIndex()][0] * 1024 * 1024
        roots = list(self._roots)
        self.tasks.submit("dup", "查找重复文件", self._make_task(roots, min_size))

    def _make_task(self, roots: list[str], min_size: int):
        def run(report, should_stop):
            groups = dup.collect(
                roots,
                min_size,
                should_stop=should_stop,
                on_progress=lambda seen, _total, found: report(
                    0,
                    f"已扫描 {seen:,} 个文件 · 找到 {found} 组"
                    if seen
                    else f"正在比对内容… 已确认 {found} 组",
                ),
            )
            if should_stop():
                return None
            return DupState(
                groups=groups,
                wasted=dup.total_wasted(groups),
                scanned_at=time.time(),
            )

        return run

    def _stop_scan(self) -> None:
        if self.tasks.is_busy("dup"):
            self.tasks.cancel("dup")
            self.lbl_stat.setText("正在停止…")

    def _on_task_progress(self, task_id: str, _percent: int, text: str) -> None:
        if task_id == "dup" and text:
            self.lbl_stat.setText(text)

    def _on_task_finished(self, task_id: str, _result) -> None:
        if task_id != "dup":
            return
        self.btn_scan.setEnabled(True)
        self.btn_stop.hide()

    def _on_task_cancelled(self, task_id: str) -> None:
        if task_id != "dup":
            return
        self.btn_scan.setEnabled(True)
        self.btn_stop.hide()
        self.lbl_stat.setText("已取消")

    def shutdown(self) -> None:
        self.tasks.cancel_many("dup")

    def _render_dup(self) -> None:
        state = self.store.dup
        self._groups = list(state.groups)
        self.btn_scan.setEnabled(True)
        self.btn_stop.hide()
        self._fill_table()

    def _fill_table(self) -> None:
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(self._groups))

        for index, group in enumerate(self._groups):
            holder = QWidget()
            layout = QHBoxLayout(holder)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setAlignment(Qt.AlignCenter)
            check = CheckBox()
            check.setChecked(True)
            check.toggled.connect(self._refresh_total)
            layout.addWidget(check)
            self.table.setCellWidget(index, 0, holder)

            keeper = group.keeper
            name_item = QTableWidgetItem(os.path.basename(keeper))
            name_item.setFont(font(12))
            name_item.setData(Qt.UserRole, index)
            name_item.setToolTip("\n".join(group.files))
            self.table.setItem(index, 1, name_item)

            count_item = QTableWidgetItem(f"×{len(group.files)}")
            count_item.setFont(font(11))
            count_item.setForeground(QColor(T.TEXT_SUB))
            self.table.setItem(index, 2, count_item)

            size_item = QTableWidgetItem(fmt_bytes(group.size))
            size_item.setFont(font(11))
            size_item.setForeground(QColor(T.TEXT_SUB))
            self.table.setItem(index, 3, size_item)

            waste_item = QTableWidgetItem(fmt_bytes(group.wasted))
            waste_item.setFont(font(11))
            waste_item.setForeground(QColor(T.AMBER))
            self.table.setItem(index, 4, waste_item)

        self.table.setUpdatesEnabled(True)
        total = dup.total_wasted(self._groups)
        self.lbl_stat.setText(
            f"{len(self._groups)} 组重复 · 共可释放 {fmt_bytes(total)}"
        )
        self._refresh_total()

    def _checked_groups(self) -> list[dup.DupGroup]:
        out = []
        for index in range(self.table.rowCount()):
            holder = self.table.cellWidget(index, 0)
            if holder is None:
                continue
            check = holder.findChild(CheckBox)
            if check is not None and check.isChecked() and index < len(self._groups):
                out.append(self._groups[index])
        return out

    def _refresh_total(self) -> None:
        groups = self._checked_groups()
        total = dup.total_wasted(groups)
        self.lbl_total.setText(
            f"已选 {len(groups)} 组 · 可释放 {fmt_bytes(total)}" if groups else "未选择"
        )

    def _toggle_all(self) -> None:
        target = len(self._checked_groups()) < self.table.rowCount()
        for index in range(self.table.rowCount()):
            holder = self.table.cellWidget(index, 0)
            if holder is None:
                continue
            check = holder.findChild(CheckBox)
            if check is not None:
                check.blockSignals(True)
                check.setChecked(target)
                check.blockSignals(False)
        self.btn_all.setText("全不选" if target else "全选")
        self._refresh_total()

    # ---------------------------------------------------------- 清理

    def _show_menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0 or row >= len(self._groups):
            return
        group = self._groups[row]
        menu = QMenu(self)
        menu.addAction("打开保留文件所在目录").triggered.connect(
            lambda: reveal_file(group.keeper)
        )
        menu.addAction("复制全部路径").triggered.connect(
            lambda: copy_text("\n".join(group.files))
        )
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _clean(self) -> None:
        groups = self._checked_groups()
        if not groups:
            self.lbl_stat.setText("没有选中任何分组")
            return
        victims = dup.cleanup_list(groups)
        total = sum(g.wasted for g in groups)

        answer = QMessageBox.question(
            self,
            "清理重复文件",
            f"将把 {len(victims)} 个重复副本送进回收站，可释放 {fmt_bytes(total)}。\n\n"
            "每组会保留最早修改的那一份，不会删光。\n"
            "文件进的是回收站，随时可以还原。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        ok, failed = trash.recycle(victims)
        message = f"已送 {ok} 个文件到回收站"
        if failed:
            message += f"，{failed} 个失败"
        # 重新扫一遍太慢，直接把已处理的分组从列表里去掉。
        # 必须写回 store：顶部「可释放」、体检都读它；只改本地 _groups 的话
        # 清完数字不动，切页面回来这些文件还会"复活"。
        done = {id(group) for group in groups}
        self.store.set_dup_groups(
            [group for group in self._groups if id(group) not in done]
        )
        # store 信号会重填表格并把统计文案改成「N 组重复」，这里再写一遍结果
        self.lbl_stat.setText(message)
