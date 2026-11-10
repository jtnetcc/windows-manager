"""软件管理页：已安装程序列表、卸载、打开安装目录。"""

from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import apps as appmgr
from core.store import SoftwareState

from . import theme_dark as T
from .components import Card, ComboBox, copy_text, font, label, make_table


class SoftwarePage(QWidget):
    # 表头列 -> 排序下拉框的索引
    SORT_BY_COLUMN = {0: 0, 3: 1, 4: 2}

    def __init__(self, store, tasks, parent=None):
        super().__init__(parent)
        self.store = store
        self.tasks = tasks
        self._apps: list[appmgr.InstalledApp] = []

        self.store.software_changed.connect(self._render_software)
        self.tasks.finished.connect(self._on_task_finished)
        self.tasks.cancelled.connect(self._on_task_cancelled)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(9)

        card = Card(padding=12, spacing=8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(7)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索程序名或发行商…")
        self.search.setFixedHeight(28)
        self.search.setFixedWidth(220)
        self.search.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self.search)

        self.sort_box = ComboBox()
        self.sort_box.addItems(["按名称排序", "按体积排序", "按安装日期排序"])
        self.sort_box.setFixedHeight(28)
        self.sort_box.setMinimumWidth(132)
        self.sort_box.currentIndexChanged.connect(self._apply_filter)
        toolbar.addWidget(self.sort_box)

        self.btn_refresh = QPushButton("重新扫描")
        self.btn_refresh.setObjectName("Ghost")
        self.btn_refresh.clicked.connect(self.refresh)
        toolbar.addWidget(self.btn_refresh)

        toolbar.addStretch(1)
        self.lbl_count = label("", 11, T.TEXT_DIM)
        toolbar.addWidget(self.lbl_count)
        card.body().addLayout(toolbar)

        self.table = make_table(
            ["程序", "版本", "发行商", "大小", "安装日期"],
            elide=Qt.ElideRight,
            menu=True,
        )
        self.table.customContextMenuRequested.connect(self._show_menu)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, 5):
            header.setSectionResizeMode(col, QHeaderView.Fixed)
        self.table.setColumnWidth(1, 110)
        self.table.setColumnWidth(2, 200)
        self.table.setColumnWidth(3, 86)
        self.table.setColumnWidth(4, 96)
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._on_header_clicked)

        card.add(self.table, 1)
        root.addWidget(card, 1)

        hint = label(
            "列表合并两个来源：注册表登记的安装包 + 安装目录里带卸载程序的软件"
            "（网游客户端、绿色版常只占后者）。卸载一律打开程序自带的向导，"
            "本工具不会静默删除文件。",
            10,
            T.TEXT_DIM,
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

    def _on_header_clicked(self, column: int) -> None:
        index = self.SORT_BY_COLUMN.get(column)
        if index is not None:
            self.sort_box.setCurrentIndex(index)

    # ---------------------------------------------------------- 数据

    def refresh(self) -> None:
        """枚举注册表比较慢，交给任务中心，别卡住界面。"""
        # 原来这里直接 return，按钮看着能点却毫无反应，
        # 体检或清理在跑时（任务中心一次只跑一个）最容易被当成「重新扫描坏了」
        if self.tasks.is_busy("software"):
            self.lbl_count.setText("已在扫描队列中，完成后自动刷新…")
            return
        waiting = self.tasks.is_busy()
        if waiting:
            self.lbl_count.setText(f"等待「{self.tasks.current_label}」结束…")
        else:
            self.lbl_count.setText("正在读取…")
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setText("排队中…" if waiting else "扫描中…")
        if not self.tasks.submit("software", "读取已安装软件", self._make_task()):
            self.btn_refresh.setEnabled(True)
            self.btn_refresh.setText("重新扫描")
            self.lbl_count.setText("无法启动扫描，请稍后再试")

    def _make_task(self):
        def run(report, should_stop):
            report(15, "正在枚举注册表…")
            # 注册表只覆盖「正规安装」的软件；网游客户端、绿色版、自家启动器
            # 装的程序压根不写 Uninstall 键，得再扫一遍安装目录才补得全。
            apps = appmgr.list_installed(
                report=lambda pct, text: report(pct, text) if not should_stop() else None
            )
            if should_stop():
                return None
            return SoftwareState(apps=apps, scanned_at=time.time())

        return run

    def _on_task_finished(self, task_id: str, _result) -> None:
        if task_id == "software":
            self._reset_refresh_button()

    def _on_task_cancelled(self, task_id: str) -> None:
        if task_id == "software":
            self._reset_refresh_button()
            self.lbl_count.setText("已取消")

    def _reset_refresh_button(self) -> None:
        self.btn_refresh.setEnabled(True)
        self.btn_refresh.setText("重新扫描")

    def _render_software(self) -> None:
        self._apps = list(self.store.software.apps)
        self._reset_refresh_button()
        self._apply_filter()

    def shutdown(self) -> None:
        if self.tasks.is_busy("software"):
            self.tasks.cancel("software")

    def _apply_filter(self) -> None:
        keyword = self.search.text().strip().lower()
        rows = self._apps
        if keyword:
            rows = [
                a for a in rows
                if keyword in a.name.lower() or keyword in a.publisher.lower()
            ]

        mode = self.sort_box.currentIndex()
        if mode == 1:
            rows = sorted(rows, key=lambda a: a.size_kb, reverse=True)
        elif mode == 2:
            rows = sorted(rows, key=lambda a: a.install_date, reverse=True)

        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(rows))

        for index, app in enumerate(rows):
            name_item = QTableWidgetItem(app.name)
            name_item.setData(Qt.UserRole, app)
            name_item.setFont(font(12))
            tip = app.name
            if app.location:
                tip += f"\n位置：{app.location}"
            if app.source == "安装目录":
                tip += "\n来源：扫描安装目录（该程序未在注册表登记卸载信息）"
            elif app.source:
                tip += f"\n来源：注册表（{app.source}）"
            if app.system:
                # 这些是别的程序带的运行时（.NET、WebView2），卸了可能连带坏一片
                tip += "\n系统组件：其他程序的运行依赖，卸载后相关软件可能无法启动"
            if not app.removable:
                tip += "\n没有可用的卸载命令，只能手动到安装目录里删"
            name_item.setToolTip(tip)
            self.table.setItem(index, 0, name_item)

            version_item = self._cell(app.version or "—", T.TEXT_SUB, 11)
            if not app.version:
                version_item.setToolTip("该程序没有登记版本号")
            elif app.source == "安装目录":
                version_item.setToolTip("版本取自卸载程序文件内的版本信息")
            self.table.setItem(index, 1, version_item)
            publisher = app.publisher or "—"
            pub_item = self._cell(publisher, T.TEXT_DIM, 11)
            pub_item.setToolTip(publisher)
            self.table.setItem(index, 2, pub_item)

            if app.size_kb:
                # 有些项登记的是几 KB 的假值（.NET Host 就是），
                # 直接按 MB 取整会显示成「0 MB」，看着像没读到
                size_text = "<1 MB" if app.size_mb < 1 else f"{app.size_mb:.0f} MB"
            else:
                size_text = "—"
            size_item = self._cell(size_text, T.TEXT, 11)
            size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if app.size_kb and app.source == "安装目录":
                size_item.setToolTip("按安装目录里所有文件的实际大小统计")
            elif not app.size_kb:
                size_item.setToolTip(
                    "注册表没有登记体积（驱动、运行库常见），"
                    "也没有可扫描的安装目录"
                )
            self.table.setItem(index, 3, size_item)

            date_text = app.install_date or "—"
            date_item = self._cell(date_text, T.TEXT_DIM, 11)
            if not app.install_date:
                date_item.setToolTip("注册表没有 InstallDate，键写入时间也取不到")
            self.table.setItem(index, 4, date_item)

        self.table.setUpdatesEnabled(True)
        total = sum(a.size_kb for a in rows) / 1024 / 1024
        self.lbl_count.setText(f"{len(rows)} 个程序 · 合计 {total:.1f} GB")

    def _cell(self, text: str, color: str, size: int) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFont(font(size))
        item.setForeground(QColor(color))
        return item

    def _app_at(self, row: int) -> appmgr.InstalledApp | None:
        item = self.table.item(row, 0)
        if not item:
            return None
        app = item.data(Qt.UserRole)
        return app if isinstance(app, appmgr.InstalledApp) else None

    # ---------------------------------------------------------- 交互

    def _show_menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        app = self._app_at(row)
        if app is None:
            return

        menu = QMenu(self)
        action = menu.addAction("卸载…")
        action.triggered.connect(lambda: self._uninstall(app))
        if not app.removable:
            action.setEnabled(False)
            action.setText("卸载…（该程序未提供卸载程序）")
        open_dir = menu.addAction("打开安装目录")
        open_dir.triggered.connect(lambda: self._open_dir(app))
        open_dir.setEnabled(bool(app.location and os.path.isdir(app.location)))
        menu.addSeparator()
        menu.addAction("复制名称").triggered.connect(
            lambda: copy_text(app.name)
        )
        menu.addAction("复制安装路径").triggered.connect(
            lambda: copy_text(app.location or app.name)
        )
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _uninstall(self, app: appmgr.InstalledApp) -> None:
        if not app.removable:
            QMessageBox.information(
                self,
                "无法卸载",
                f"{app.name} 没有提供卸载程序。\n\n"
                "这类程序一般是解压即用或由启动器安装，需要手动删除安装目录。",
            )
            return
        answer = QMessageBox.question(
            self,
            "卸载程序",
            f"将启动 {app.name} 自带的卸载向导。\n\n"
            "请在弹出的窗口中确认操作，本工具不会替你点「下一步」。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        ok, err = appmgr.launch_uninstaller(app)
        if not ok:
            QMessageBox.warning(self, "无法启动卸载程序", err)

    def _open_dir(self, app: appmgr.InstalledApp) -> None:
        ok, err = appmgr.open_install_dir(app)
        if not ok:
            QMessageBox.information(self, "无法打开目录", err)
