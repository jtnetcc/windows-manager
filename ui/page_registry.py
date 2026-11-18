"""注册表残留视图：扫描冗余项，备份后清理。

**它已经不是一个独立页面了**：侧栏里原来那行「注册表项」常年是「没有发现
冗余项」，而注册表清理对现代 Windows 基本没有提速作用（系统按索引访问，
不是全表扫描），却是全软件唯一会删注册表的地方。现在它作为「软件管理」
的第三段存在 —— 和「卸载残留」同页，两个同名不同物的概念终于挨在了一起。

设计上刻意保守：
- 扫描只读，不改任何东西
- 删除前**强制导出 .reg 备份**，没有备份就不给删
- 默认不勾选任何项，用户必须自己选
"""

from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
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

from core import registry
from core.store import RegistryState

from . import theme_dark as T
from .components import Card, label


def make_task():
    """扫一次注册表的任务体。

    体检的 `run_full_check` 也要这一份：它以前靠「把页面建出来再点扫描」
    拿到 registry_count，页面并进软件管理之后没那么方便了，直接派任务更省。
    """

    def run(report, should_stop):
        issues = registry.scan(
            should_stop=should_stop,
            on_progress=lambda percent, text: report(percent, text),
        )
        if should_stop():
            return None
        return RegistryState(issues=issues, scanned_at=time.time())

    return run


class RegistryPage(QWidget):
    COLUMNS = ("类型", "项目", "原因", "详情")

    def __init__(self, store, tasks, parent=None):
        super().__init__(parent)
        self.store = store
        self.tasks = tasks
        self._issues: list[registry.Issue] = []

        self.store.registry_changed.connect(self._render)
        self.tasks.progress.connect(self._on_task_progress)
        self.tasks.finished.connect(self._on_task_finished)
        self.tasks.cancelled.connect(self._on_task_cancelled)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.lbl_stat = label("点右侧「扫描注册表」，只读检查，不到 1 秒", 12, T.TEXT_DIM)
        bar.addWidget(self.lbl_stat, 0)
        bar.addStretch(1)

        self.btn_backup = QPushButton("导出备份")
        self.btn_backup.setObjectName("Ghost")
        self.btn_backup.setFixedHeight(30)
        self.btn_backup.setCursor(Qt.PointingHandCursor)
        self.btn_backup.clicked.connect(self._export)
        self.btn_backup.setEnabled(False)
        bar.addWidget(self.btn_backup)

        self.btn_delete = QPushButton("删除选中")
        self.btn_delete.setObjectName("Ghost")
        self.btn_delete.setFixedHeight(30)
        self.btn_delete.setCursor(Qt.PointingHandCursor)
        self.btn_delete.clicked.connect(self._delete_selected)
        self.btn_delete.setEnabled(False)
        bar.addWidget(self.btn_delete)

        self.btn_scan = QPushButton("扫描注册表")
        self.btn_scan.setObjectName("Primary")
        self.btn_scan.setFixedHeight(30)
        self.btn_scan.setCursor(Qt.PointingHandCursor)
        self.btn_scan.clicked.connect(self.start_scan)
        bar.addWidget(self.btn_scan)

        card = Card(padding=10, spacing=8)
        # 工具条放进卡片里：页面顶部只留标题和全局动作，否则两行按钮各压一个
        # 蓝块、右边缘还对齐成一列，看着像表格里的两列
        card.body().addLayout(bar)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        # 其余页都用 make_table，表头左对齐；这三页是手写的，
        # 不补这一行表头会居中，和单元格左对齐的正文错位
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 82)
        self.table.setColumnWidth(1, 210)
        self.table.setColumnWidth(3, 260)
        self.table.verticalHeader().setDefaultSectionSize(30)
        card.body().addWidget(self.table, 1)

        # 空状态：这一段绝大多数时候就是「什么都没有」。留一张空表让人对着
        # 一片白，会以为还在扫或者自己点错了；摆一句明确结论，比空的表头有用。
        self.empty = QWidget()
        empty_box = QVBoxLayout(self.empty)
        empty_box.setContentsMargins(0, 0, 0, 0)
        empty_box.setSpacing(6)
        empty_box.addStretch(1)
        title = label("没有发现失效的注册表条目", 13, T.TEXT, QFont.DemiBold)
        title.setAlignment(Qt.AlignCenter)
        note = label(
            "失效的卸载项、启动命令、App Paths 与 COM 登记都会列在这里。\n"
            "现在的结果是干净的 —— 装过又卸掉的软件最容易留下这类条目。",
            11,
            T.TEXT_DIM,
        )
        note.setAlignment(Qt.AlignCenter)
        empty_box.addWidget(title)
        empty_box.addWidget(note)
        empty_box.addStretch(1)
        self.empty.setVisible(False)
        card.body().addWidget(self.empty, 1)

        root.addWidget(card, 1)

        self.lbl_hint = label(
            "删除前会自动导出 .reg 备份到「文档」目录。",
            11,
            T.TEXT_DIM,
        )
        root.addWidget(self.lbl_hint)

    # ---------------------------------------------------------- 扫描

    def ensure_scan(self) -> None:
        """切到这一段时调：有结果就只重画，没有才扫。

        扫一次 0.04 秒，但没必要每次切段都重扫 —— 换肤重建、来回切段都会
        走到这里，重扫会让表在眼前闪一下、滚动位置也丢。
        """
        if self.store.registry.ready:
            self._render()
            return
        if not self.tasks.is_busy("registry"):
            self.start_scan()

    def start_scan(self) -> None:
        if self.tasks.is_busy("registry"):
            return
        self.lbl_stat.setText("正在扫描…")
        self.btn_scan.setEnabled(False)
        self.tasks.submit("registry", "扫描注册表", make_task())

    def _on_task_progress(self, task_id: str, _percent: int, text: str) -> None:
        if task_id == "registry" and text:
            self.lbl_stat.setText(text)

    def _on_task_finished(self, task_id: str, _result) -> None:
        if task_id == "registry":
            self.btn_scan.setEnabled(True)

    def _on_task_cancelled(self, task_id: str) -> None:
        if task_id != "registry":
            return
        self.btn_scan.setEnabled(True)
        self.lbl_stat.setText("已取消")

    def _render(self) -> None:
        state = self.store.registry
        self._issues = list(state.issues)
        self.btn_scan.setEnabled(True)
        self.btn_scan.setText("重新扫描")

        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(self._issues))
        for row, issue in enumerate(self._issues):
            for col, text in enumerate(
                (issue.kind_text, issue.title, issue.reason, issue.detail)
            ):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setForeground(
                        Qt.GlobalColor.gray if issue.needs_admin else Qt.GlobalColor.white
                    )
                item.setToolTip(text)
                self.table.setItem(row, col, item)
        self.table.setUpdatesEnabled(True)

        count = len(self._issues)
        admin = sum(1 for i in self._issues if i.needs_admin)
        # 空表换成一句结论：表头和整片空白会让人以为「还没扫完」
        self.table.setVisible(count > 0)
        self.empty.setVisible(count == 0)
        if count == 0:
            # 结论交给中间那块空态说，这里只留状态，免得同一句话出现两遍
            self.lbl_stat.setText("扫描完成")
        else:
            extra = f"，其中 {admin} 项在系统级位置" if admin else ""
            self.lbl_stat.setText(f"发现 {count} 项冗余{extra}")
        self.btn_backup.setEnabled(count > 0)
        self.btn_delete.setEnabled(count > 0)

    # ---------------------------------------------------------- 操作

    def _selected(self) -> list[registry.Issue]:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        if not rows:
            return []
        return [self._issues[r] for r in rows if r < len(self._issues)]

    def _export(self) -> None:
        targets = self._selected() or self._issues
        path = self._backup_path()
        written = registry.export_backup(targets, path)
        if written:
            self.lbl_hint.setText(f"已导出 {written} 项到 {path}")
        else:
            self.lbl_hint.setText("导出失败，请检查目录权限")

    def _backup_path(self) -> str:
        folder = os.path.join(os.path.expanduser("~"), "Documents")
        if not os.path.isdir(folder):
            folder = os.environ.get("TEMP", ".")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return os.path.join(folder, f"注册表备份-{stamp}.reg")

    def _delete_selected(self) -> None:
        targets = self._selected()
        if not targets:
            QMessageBox.information(
                self, "未选择", "请先在列表里选中要删除的项。"
            )
            return

        admin = sum(1 for i in targets if i.needs_admin)
        tip = f"将删除 {len(targets)} 项注册表条目。"
        if admin:
            tip += f"\n其中 {admin} 项在系统级位置。"

        box = QMessageBox(self)
        box.setWindowTitle("确认删除")
        box.setText(tip + "\n\n删除前会自动导出一份 .reg 备份。")
        box.setIcon(QMessageBox.Warning)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        if box.exec() != QMessageBox.Yes:
            return

        # 备份是删除的前置条件：备份失败就不删
        path = self._backup_path()
        if registry.export_backup(targets, path) <= 0:
            QMessageBox.warning(self, "备份失败", "备份没写成功，为安全起见已取消删除。")
            return

        ok, failed = registry.delete(targets)
        remaining = [i for i in self._issues if i not in targets]
        self.store.apply(
            "registry",
            RegistryState(issues=remaining, scanned_at=time.time()),
        )
        message = f"已删除 {ok} 项"
        if failed:
            message += f"，{failed} 项失败（多半是权限不足）"
        message += f"\n备份：{path}"
        self.lbl_hint.setText(message.replace("\n", " · "))

    def shutdown(self) -> None:
        self.tasks.cancel_many("registry")
