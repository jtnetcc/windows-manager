"""软件管理页：两段 ——「已安装程序」和「卸载残留」。

两段放在一起是因为它们回答的是同一个问题的两面：这台机器上装了哪些软件、
卸掉之后留下了什么。做成两个导航项会让人以为要分两次操作。

卸载残留那段风险很高，规则写在 `core/leftover.py` 的文件头：只列不勾选、
四道闸门、删除走回收站。这一页的职责是**把不确定性说清楚**，不是替用户下判断。
"""

from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import apps as appmgr
from core import leftover
from core.store import SoftwareState

from . import theme_dark as T
from .components import (
    Card,
    CheckBox,
    ComboBox,
    Segmented,
    copy_text,
    font,
    label,
    make_table,
)

SEGMENTS = [
    ("installed", "已安装程序"),
    ("leftover", "卸载残留"),
]

SEGMENT_SUBS = {
    "installed": "已安装程序与卸载入口",
    "leftover": "卸载后留下的无主数据目录，只列出、不自动勾选",
}


class SoftwarePage(QWidget):
    """两段式容器。对外仍然是 `SoftwarePage`，主窗口那一侧不用改。"""

    segment_changed = Signal(str, str)

    def __init__(self, store, tasks, parent=None):
        super().__init__(parent)
        self._key = SEGMENTS[0][0]

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

        self.installed = InstalledView(store, tasks)
        self.leftover = LeftoverView(store, tasks)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.installed)
        self.stack.addWidget(self.leftover)
        root.addWidget(self.stack, 1)

    def _on_segment(self, key: str) -> None:
        self._key = key
        self.stack.setCurrentWidget(
            self.installed if key == "installed" else self.leftover
        )
        self.segment_changed.emit(key, SEGMENT_SUBS.get(key, ""))
        if key == "leftover":
            self.leftover.ensure_scan()
        else:
            self.installed.ensure_scan()

    def set_segment(self, key: str) -> None:
        if key not in dict(SEGMENTS):
            return
        self.seg.set_current(key, emit=False)
        self._on_segment(key)

    def current_segment(self) -> str:
        return self._key

    def subtitle(self) -> str:
        return SEGMENT_SUBS.get(self._key, "")

    def start_scan(self) -> None:
        if self._key == "installed":
            self.installed.refresh()
        else:
            self.leftover.ensure_scan(force=True)

    def shutdown(self) -> None:
        self.installed.shutdown()
        self.leftover.shutdown()


class InstalledView(QWidget):
    """已安装程序列表：搜索、排序、右键卸载。"""

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

    def ensure_scan(self) -> None:
        """切到这一段时，没扫过才扫。"""
        if self.store.software.ready or self.tasks.is_busy("software"):
            return
        self.refresh()

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
        self.tasks.cancel_many("software")

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


class LeftoverView(QWidget):
    """卸载残留：只列出候选，默认一个都不勾。"""

    COLUMNS = ("", "目录", "位置", "大小", "最后修改", "风险")

    def __init__(self, store, tasks, parent=None):
        super().__init__(parent)
        self.store = store
        self.tasks = tasks
        self._items: list[leftover.Leftover] = []
        self._boxes: list[CheckBox] = []
        self._scanned = False

        self.tasks.finished.connect(self._on_task_finished)
        self.tasks.cancelled.connect(self._on_task_cancelled)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # 风险提示放在最上面，先让用户知道这是「猜」出来的。
        # 用富文本而不是 Markdown：QLabel 的 Markdown 解析会被样式表里的
        # font-size 干扰，实测直接把 ** 当字面量画出来。
        self.tip = QLabel(
            "判断依据：目录名匹配不上任何已安装软件、正在运行的程序，"
            "盘上也找不到同名程序目录，且长期没有改动。<br>"
            "这是启发式判断，<b>不保证准确</b>。默认一个都不勾选，"
            "删除走回收站，误删了可以还原。"
        )
        self.tip.setWordWrap(True)
        self.tip.setTextFormat(Qt.RichText)
        self.tip.setStyleSheet(
            f"background: {T.with_alpha(T.AMBER, 0.10)};"
            f" border: 1px solid {T.with_alpha(T.AMBER, 0.34)};"
            f" border-radius: 4px; padding: 8px 10px;"
            f" color: {T.TEXT_SUB}; font-size: 11px;"
        )
        root.addWidget(self.tip)

        card = Card(padding=12, spacing=8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(7)
        self.btn_scan = QPushButton("扫描残留")
        self.btn_scan.setObjectName("Ghost")
        self.btn_scan.clicked.connect(lambda: self.ensure_scan(force=True))
        toolbar.addWidget(self.btn_scan)

        self.btn_toggle = QPushButton("全选")
        self.btn_toggle.setObjectName("Ghost")
        self.btn_toggle.clicked.connect(self._toggle_all)
        self.btn_toggle.setEnabled(False)
        toolbar.addWidget(self.btn_toggle)

        toolbar.addStretch(1)
        self.lbl_stat = label("尚未扫描", 11, T.TEXT_DIM)
        toolbar.addWidget(self.lbl_stat)
        card.body().addLayout(toolbar)

        self.table = make_table(list(self.COLUMNS), elide=Qt.ElideMiddle, menu=True)
        self.table.customContextMenuRequested.connect(self._show_menu)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        header.setSectionResizeMode(4, QHeaderView.Fixed)
        header.setSectionResizeMode(5, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 34)
        self.table.setColumnWidth(2, 116)
        self.table.setColumnWidth(3, 84)
        self.table.setColumnWidth(4, 96)
        self.table.setColumnWidth(5, 64)
        card.add(self.table, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.lbl_sel = label("", 11, T.TEXT_DIM)
        bottom.addWidget(self.lbl_sel, 1)
        self.btn_delete = QPushButton("删除选中项…")
        self.btn_delete.setObjectName("Primary")
        self.btn_delete.setFixedHeight(30)
        self.btn_delete.setCursor(Qt.PointingHandCursor)
        self.btn_delete.setEnabled(False)
        self.btn_delete.clicked.connect(self._delete_selected)
        bottom.addWidget(self.btn_delete)
        card.body().addLayout(bottom)

        root.addWidget(card, 1)

    # ---------------------------------------------------------- 扫描

    def ensure_scan(self, force: bool = False) -> None:
        if self.tasks.is_busy("leftover"):
            return
        if self._scanned and not force:
            return
        self.btn_scan.setEnabled(False)
        self.lbl_stat.setText("正在扫描…")
        self.tasks.submit("leftover", "查找卸载残留", self._make_task())

    def _make_task(self):
        def run(report, should_stop):
            installed = list(self.store.software.apps)
            if not installed:
                # 用户可能直接点了这一段，没先看「已安装程序」。
                # 判定全靠这份清单，没有它就会把在用的目录误判成残留。
                report(8, "正在读取已安装软件…")
                installed = appmgr.list_installed(with_folders=True)
            if should_stop():
                return None
            report(20, "正在比对目录归属…")
            return leftover.scan(
                installed,
                report=lambda pct, text: report(pct, text) if not should_stop() else None,
                should_stop=should_stop,
            )

        return run

    def _on_task_finished(self, task_id: str, result) -> None:
        if task_id != "leftover":
            return
        self.btn_scan.setEnabled(True)
        self.btn_scan.setText("重新扫描")
        if result is None:
            self.lbl_stat.setText("扫描未完成")
            return
        self._scanned = True
        self._items = list(getattr(result, "items", []) or [])
        self._render(result)

    def _on_task_cancelled(self, task_id: str) -> None:
        if task_id == "leftover":
            self.btn_scan.setEnabled(True)
            self.lbl_stat.setText("已取消")

    def shutdown(self) -> None:
        self.tasks.cancel_many("leftover")

    # ---------------------------------------------------------- 渲染

    def _render(self, result=None) -> None:
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(self._items))
        self._boxes = []

        for index, item in enumerate(self._items):
            box = CheckBox()
            # CheckBox 默认是勾上的（清理页要「全选」），这里必须显式取消：
            # 残留是猜出来的，一个都不能替用户预先勾。
            box.setChecked(False)
            box.toggled.connect(self._refresh_selection)
            holder = QWidget()
            layout = QHBoxLayout(holder)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setAlignment(Qt.AlignCenter)
            layout.addWidget(box)
            self.table.setCellWidget(index, 0, holder)
            self._boxes.append(box)

            name_item = QTableWidgetItem(item.name)
            name_item.setFont(font(12))
            name_item.setToolTip(f"{item.path}\n{item.note}")
            self.table.setItem(index, 1, name_item)

            root_item = self._cell(item.root_label, T.TEXT_DIM, 11)
            root_item.setToolTip(item.path)
            self.table.setItem(index, 2, root_item)

            size_text = (
                f"{item.size / 1024 / 1024:.1f} MB"
                if item.size >= 1024 * 1024
                else f"{item.size / 1024:.0f} KB"
            )
            size_item = self._cell(size_text, T.TEXT, 11)
            size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(index, 3, size_item)

            stamp = (
                time.strftime("%Y-%m-%d", time.localtime(item.mtime))
                if item.mtime
                else "—"
            )
            self.table.setItem(index, 4, self._cell(stamp, T.TEXT_DIM, 11))

            level_text, level_color = {
                "low": ("低", T.GREEN),
                "mid": ("中", T.AMBER),
                "high": ("高", T.RED),
            }.get(item.risk, ("中", T.AMBER))
            risk_item = self._cell(level_text, level_color, 11)
            risk_item.setToolTip(item.note)
            self.table.setItem(index, 5, risk_item)

        self.table.setUpdatesEnabled(True)
        self.btn_toggle.setEnabled(bool(self._items))
        self.btn_toggle.setText("全选")

        total = sum(i.size for i in self._items)
        if not self._items:
            self.lbl_stat.setText("没有发现无主目录")
        else:
            note = (
                f"{len(self._items)} 个候选 · 合计 {total / 1073741824:.2f} GB"
                if total >= 1024 ** 3
                else f"{len(self._items)} 个候选 · 合计 {total / 1048576:.1f} MB"
            )
            if result is not None and getattr(result, "claimed", 0):
                note += f" · 已排除 {result.claimed} 个有主的"
            self.lbl_stat.setText(note)
        self._refresh_selection()

    def _cell(self, text: str, color: str, size: int) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFont(font(size))
        item.setForeground(QColor(color))
        return item

    # ---------------------------------------------------------- 选择

    def _checked_rows(self) -> list[int]:
        return [i for i, box in enumerate(self._boxes) if box.isChecked()]

    def _toggle_all(self) -> None:
        target = not all(box.isChecked() for box in self._boxes) if self._boxes else False
        for box in self._boxes:
            box.setChecked(target)
        self.btn_toggle.setText("全不选" if target else "全选")
        self._refresh_selection()

    def _refresh_selection(self) -> None:
        rows = self._checked_rows()
        self.btn_delete.setEnabled(bool(rows))
        if not rows:
            self.lbl_sel.setText("未选中任何目录")
            return
        size = sum(self._items[i].size for i in rows if i < len(self._items))
        text = (
            f"已选 {len(rows)} 项 · {size / 1073741824:.2f} GB"
            if size >= 1024 ** 3
            else f"已选 {len(rows)} 项 · {size / 1048576:.1f} MB"
        )
        high = sum(
            1 for i in rows if i < len(self._items) and self._items[i].risk == "high"
        )
        if high:
            text += f"（含 {high} 项近期改动过）"
        self.lbl_sel.setText(text)

    # ---------------------------------------------------------- 删除

    def _show_menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0 or row >= len(self._items):
            return
        item = self._items[row]
        menu = QMenu(self)
        menu.addAction("打开目录").triggered.connect(
            lambda: os.startfile(item.path) if os.path.isdir(item.path) else None
        )
        menu.addAction("复制完整路径").triggered.connect(
            lambda: copy_text(item.path)
        )
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _delete_selected(self) -> None:
        rows = self._checked_rows()
        targets = [self._items[i] for i in rows if i < len(self._items)]
        if not targets:
            return

        total = sum(t.size for t in targets)
        preview = "\n".join(f"· {t.name}" for t in targets[:8])
        if len(targets) > 8:
            preview += f"\n… 等 {len(targets)} 个目录"

        answer = QMessageBox.warning(
            self,
            "确认删除",
            f"将把以下 {len(targets)} 个目录（约 {total / 1048576:.1f} MB）"
            f"送到回收站：\n\n{preview}\n\n"
            "判断依据是「没有软件认领 + 长期未改动」，不保证一定准确。\n"
            "如果某个目录属于你正在使用的软件，请取消勾选。\n\n"
            "删除后可以从回收站还原。",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return

        ok, failed, freed = leftover.remove([t.path for t in targets])
        self._scanned = False
        parts = [f"已删除 {ok} 个目录"]
        if freed:
            parts.append(f"释放 {freed / 1048576:.1f} MB")
        if failed:
            parts.append(f"{failed} 个失败（可能被占用）")
        self.lbl_stat.setText(" · ".join(parts))
        self.ensure_scan(force=True)
