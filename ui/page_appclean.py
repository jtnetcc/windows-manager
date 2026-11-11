"""软件专清：按软件维度清理缓存与已接收文件。

页面结构是「左软件 / 右明细」：

- 左侧一列软件，各自带体积和一条占比条，点哪一行看哪一行；
- 右侧是当前软件的分类明细，勾选后底部按钮清理。

为什么不是再做一张「全选 → 清理」的表：垃圾清理页已经是那个形态了。
软件专清要回答的是「微信到底吃了多少、清哪一类不会丢东西」，所以按软件
拆开、每类给一句「删了会怎样」，默认全部不勾 —— 删掉的是用户真收到过的
图片和文件，不能替他做主。
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core import appclean
from core.metrics import fmt_bytes

from . import theme_dark as T
from .components import Card, CheckBox, HoverMixin, MiniBar, SegmentBar, label

KIND_COLORS = {
    "image": T.CYAN,
    "video": T.PURPLE,
    "file": T.ACCENT,
    "emoji": T.AMBER,
    "cache": T.GREEN,
    "log": T.TEXT_DIM,
}


def _short_path(path: str, keep_tail: int = 3) -> str:
    """长路径只留盘符和最后几级目录。

    按字符掐中间会把 `Google` 切成 `Go…le`，读起来像乱码；按目录切分后
    变成 `C:\\…\\User Data\\Default\\Cache`，用户一眼能认出是哪个目录。
    """
    parts = path.split("\\")
    if len(parts) <= keep_tail + 2:
        return path
    return "\\".join(parts[:2] + ["…"] + parts[-keep_tail:])


class AppRow(HoverMixin, QWidget):
    """左侧一个软件：名称 + 体积 + 类目数 + 占比条。整行可点。"""

    HEIGHT = 64
    activated = Signal(str)

    def __init__(self, app: appclean.App, parent=None):
        super().__init__(parent)
        self.app = app
        self._selected = False
        self.setFixedHeight(self.HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)

        box = QVBoxLayout(self)
        box.setContentsMargins(12, 9, 12, 9)
        box.setSpacing(4)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        self.lbl_name = label(app.name, 13, T.TEXT, QFont.DemiBold)
        self.lbl_size = label("—", 12, T.TEXT_SUB)
        head.addWidget(self.lbl_name, 0)
        head.addStretch(1)
        head.addWidget(self.lbl_size, 0)
        box.addLayout(head)

        self.lbl_sub = label("", 10, T.TEXT_DIM)
        box.addWidget(self.lbl_sub)

        self.bar = MiniBar(height=4)
        box.addWidget(self.bar)

    def set_stat(self, size: int, count: int, kinds: int, ratio: float) -> None:
        self.lbl_size.setText(fmt_bytes(size) if size else "空闲")
        color = T.ACCENT if size else T.TEXT_DIM
        self.lbl_size.setStyleSheet(
            f"color: {color}; background: transparent; border: none;"
        )
        self.lbl_sub.setText(
            f"{kinds} 类 · {count:,} 个文件" if count else "没有可清理的内容"
        )
        self.bar.set_value(max(0.02, ratio), T.ACCENT if size else T.BORDER_SOFT)
        self.setToolTip(f"{self.app.name} · {count:,} 个文件\n{self.app.hint}")

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.activated.emit(self.app.key)
            event.accept()
            return
        super().mouseReleaseEvent(event)


    def paintEvent(self, event) -> None:
        if not (self._hover or self._selected):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(T.ACCENT_SOFT if self._selected else T.HOVER))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0, 1, 0, -1), 6, 6)
        if self._selected:
            # 左侧一根 2px 竖条：和侧栏选中的视觉语言保持一致
            painter.setBrush(QColor(T.ACCENT))
            painter.drawRoundedRect(QRectF(2, 10, 2.5, self.height() - 20), 1.2, 1.2)
        painter.end()


class KindRow(HoverMixin, QWidget):
    """右侧一类内容：勾选框 + 名称 + 数字，第二行是「删了会怎样」。

    说明文字必须单独一行：放在同一行时被右侧两列数字挤成「删了会重新下…」，
    而这句正是用户决定勾不勾的依据，截断等于没说。代价是行高 32 → 46。
    """

    HEIGHT = 46

    def __init__(self, item: appclean.TargetStat, checked: bool, parent=None):
        super().__init__(parent)
        self.item = item
        self.setFixedHeight(self.HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)

        box = QHBoxLayout(self)
        box.setContentsMargins(6, 4, 8, 4)
        box.setSpacing(9)

        self.check = CheckBox()
        self.check.setChecked(checked)
        box.addWidget(self.check, 0, Qt.AlignTop)

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(2)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)

        dot = QLabel()
        dot.setFixedSize(6, 6)
        dot.setStyleSheet(
            f"background: {KIND_COLORS.get(item.target.kind, T.ACCENT)};"
            f" border-radius: 3px; border: none;"
        )
        head.addWidget(dot, 0)

        name = label(item.target.name, 12, T.TEXT)
        name.setFixedWidth(96)
        head.addWidget(name, 0)
        head.addStretch(1)

        self.lbl_count = label("", 10, T.TEXT_DIM)
        self.lbl_count.setFixedWidth(82)
        self.lbl_count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        head.addWidget(self.lbl_count, 0)

        self.lbl_size = label("", 12, T.TEXT, QFont.DemiBold)
        self.lbl_size.setFixedWidth(76)
        self.lbl_size.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        head.addWidget(self.lbl_size, 0)
        body.addLayout(head)

        note = label(item.target.note, 10, T.TEXT_DIM)
        # 左侧缩进对齐名称（dot 6 + 间距 8），读起来像这条类目的注释
        note.setContentsMargins(14, 0, 0, 0)
        note.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        body.addWidget(note)

        box.addLayout(body, 1)
        self.set_stat(item)

    def set_stat(self, item: appclean.TargetStat) -> None:
        self.item = item
        empty = item.count == 0
        self.lbl_count.setText("—" if empty else f"{item.count:,} 个")
        self.lbl_size.setText("已清空" if empty else fmt_bytes(item.size))
        color = T.TEXT_DIM if empty else T.TEXT
        self.lbl_size.setStyleSheet(
            f"color: {color}; background: transparent; border: none;"
        )
        # 没有内容的类目不给勾：勾了也只是白跑一趟删除
        self.check.setEnabled(not empty)
        self.setCursor(Qt.ArrowCursor if empty else Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        if (
            event.button() == Qt.LeftButton
            and self.check.isEnabled()
            and self.rect().contains(event.position().toPoint())
        ):
            self.check.toggle()
            event.accept()
            return
        super().mouseReleaseEvent(event)


    def paintEvent(self, event) -> None:
        if not self._hover or not self.check.isEnabled():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(T.HOVER))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0, 1, 0, -1), 5, 5)
        painter.end()


class AppCleanPage(QWidget):
    def __init__(self, store, tasks, parent=None):
        super().__init__(parent)
        self.store = store
        self.tasks = tasks
        self._stats: list[appclean.AppStat] = []
        self._current = ""
        # 勾选跨软件保留：一次可以把微信的图片和 Chrome 的缓存一起清掉
        self._checked: set[str] = set()
        self._purge_keys: set[str] = set()
        self._phase = "idle"  # idle / scanning / ready / cleaning / done

        self.tasks.finished.connect(self._on_task_finished)
        self.tasks.cancelled.connect(self._on_task_cancelled)
        self.tasks.progress.connect(self._on_task_progress)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(9)

        # ---------------------------------------------------------- 左：软件
        left = Card(padding=12, spacing=0)
        head = QHBoxLayout()
        head.setContentsMargins(6, 0, 6, 0)
        head.setSpacing(6)
        head.addWidget(label("软件", 14, T.TEXT, QFont.DemiBold))
        head.addStretch(1)
        self.lbl_left = label("尚未扫描", 10, T.TEXT_DIM)
        head.addWidget(self.lbl_left)
        left.body().addLayout(head)
        left.body().addSpacing(6)

        self._app_box = QVBoxLayout()
        self._app_box.setSpacing(2)
        left.body().addLayout(self._app_box)
        left.body().addStretch(1)

        line = QLabel()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {T.BORDER_SOFT}; border: none;")
        left.body().addWidget(line)
        left.body().addSpacing(6)

        self.lbl_sum = label("合计 —", 12, T.TEXT, QFont.DemiBold)
        left.body().addWidget(self.lbl_sum)
        left.body().addSpacing(2)

        hint = label(
            "只清缓存和已收到的图片视频，聊天记录数据库不会被动。"
            "与垃圾清理的「社交缓存」是同一批目录，清一处即可。",
            10,
            T.TEXT_DIM,
        )
        hint.setWordWrap(True)
        left.body().addWidget(hint)
        left.setFixedWidth(252)
        root.addWidget(left, 0)

        # ---------------------------------------------------------- 右：明细
        right = Card(padding=14, spacing=0)
        rhead = QHBoxLayout()
        rhead.setContentsMargins(0, 0, 0, 0)
        rhead.setSpacing(6)
        self.lbl_app = label("软件专清", 18, T.TEXT, QFont.DemiBold)
        rhead.addWidget(self.lbl_app)
        rhead.addStretch(1)
        self.lbl_kinds = label("", 10, T.TEXT_DIM)
        rhead.addWidget(self.lbl_kinds)
        right.body().addLayout(rhead)

        self.lbl_hint = label(
            "微信、QQ、企业微信、钉钉与浏览器的缓存，都可以在这里分开清理。",
            11,
            T.TEXT_DIM,
        )
        self.lbl_hint.setWordWrap(True)
        right.body().addWidget(self.lbl_hint)
        right.body().addSpacing(8)

        total_row = QHBoxLayout()
        total_row.setContentsMargins(0, 0, 0, 0)
        total_row.setSpacing(8)
        self.lbl_total = label("—", 28, T.TEXT, QFont.DemiBold)
        total_row.addWidget(self.lbl_total, 0)
        self.lbl_total_note = label("", 10, T.TEXT_DIM)
        total_row.addWidget(self.lbl_total_note, 0, Qt.AlignBottom)
        total_row.addStretch(1)
        right.body().addLayout(total_row)

        self.bar = SegmentBar(height=8)
        self.bar.hide()
        right.body().addWidget(self.bar)
        right.body().addSpacing(8)

        self._kind_box = QVBoxLayout()
        self._kind_box.setSpacing(1)
        right.body().addLayout(self._kind_box)

        # 清理位置：把真实目录列出来。清理工具最大的信任来源就是「你看得见
        # 我要动哪个目录」，顺带把这半张卡片的留白填上。
        self._path_title = label("清理位置", 10, T.TEXT_DIM)
        self._path_title.hide()
        right.body().addSpacing(8)
        right.body().addWidget(self._path_title)

        self._path_box = QVBoxLayout()
        self._path_box.setSpacing(1)
        right.body().addLayout(self._path_box)
        self._path_rows: list[QLabel] = []

        # 状态紧跟内容：贴着底部按钮的话，中间一大段空白会把它和类目割开
        right.body().addSpacing(8)
        self.lbl_state = label("", 10, T.TEXT_SUB)
        self.lbl_state.setWordWrap(True)
        right.body().addWidget(self.lbl_state)

        right.body().addStretch(1)

        self.progress = MiniBar(height=4)
        self.progress.hide()
        right.body().addWidget(self.progress)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.lbl_pick = label("已选 0 项", 11, T.TEXT_SUB)
        footer.addWidget(self.lbl_pick, 1)
        self.btn_scan = QPushButton("重新扫描")
        self.btn_scan.setObjectName("Ghost")
        self.btn_scan.setFixedHeight(32)
        self.btn_scan.setCursor(Qt.PointingHandCursor)
        self.btn_scan.clicked.connect(self._on_scan_button)
        footer.addWidget(self.btn_scan, 0)
        self.btn_clean = QPushButton("清理选中")
        self.btn_clean.setObjectName("Primary")
        self.btn_clean.setFixedHeight(32)
        self.btn_clean.setCursor(Qt.PointingHandCursor)
        self.btn_clean.clicked.connect(self._on_clean)
        footer.addWidget(self.btn_clean, 0)
        right.body().addLayout(footer)

        root.addWidget(right, 1)

        self._rows: list[KindRow] = []
        self._app_rows: list[AppRow] = []
        self._sync_buttons()

    # ---------------------------------------------------------- 扫描

    def start_scan(self) -> None:
        if self.tasks.is_busy("appclean_scan"):
            return
        self._phase = "scanning"
        self._purge_keys = set()
        self.lbl_state.setText("正在统计各软件的缓存体积…")
        self.progress.set_value(0, T.ACCENT)
        self.progress.show()
        self._sync_buttons()
        self.tasks.submit("appclean_scan", "扫描软件缓存", self._make_scan_task())

    def _make_scan_task(self):
        def run(report, should_stop):
            return appclean.scan(
                should_stop=should_stop,
                on_progress=lambda percent, text: report(percent, text),
            )

        return run

    def _on_scan_button(self) -> None:
        if self._phase == "scanning":
            self.tasks.cancel("appclean_scan")
            return
        self.start_scan()

    def _on_task_progress(self, task_id: str, percent: int, text: str) -> None:
        if task_id == "appclean_scan":
            self.progress.set_value(percent / 100, T.ACCENT)
            self.lbl_state.setText(text)

    def _on_task_finished(self, task_id: str, result) -> None:
        if task_id == "appclean_scan":
            self._stats = result or []
            self._phase = "ready"
            self.progress.hide()
            self._checked = {k for k in self._checked if self._find_target(k)}
            self._rebuild_apps()
            self._sync_buttons()
            self._render_detail()
            if not self._stats:
                self.lbl_state.setText(
                    "没有发现可清理的软件缓存。装了微信、QQ、企业版微信、钉钉"
                    "或 Chrome / Edge / Firefox 之后再扫一次。"
                )
            else:
                size = sum(stat.size for stat in self._stats)
                self.lbl_state.setText(
                    f"扫描完成：{len(self._stats)} 个软件 · {fmt_bytes(size)} 可清理"
                )
            return

        if task_id == "appclean_purge":
            self._apply_purge(result)

    def _on_task_cancelled(self, task_id: str) -> None:
        if task_id in ("appclean_scan", "appclean_purge"):
            self._phase = "ready" if self._stats else "idle"
            self.progress.hide()
            self._sync_buttons()
            self.lbl_state.setText("已取消")

    # ---------------------------------------------------------- 渲染

    def _find_target(self, key: str) -> appclean.TargetStat | None:
        for stat in self._stats:
            for item in stat.items:
                if item.target.key == key:
                    return item
        return None

    def _rebuild_apps(self) -> None:
        for row in self._app_rows:
            row.setParent(None)
            row.deleteLater()
        self._app_rows.clear()

        biggest = max((stat.size for stat in self._stats), default=0) or 1
        keys = {stat.app.key for stat in self._stats}
        if self._current not in keys:
            self._current = self._stats[0].app.key if self._stats else ""
        for stat in self._stats:
            row = AppRow(stat.app)
            row.set_stat(stat.size, stat.count, stat.kinds, stat.size / biggest)
            row.activated.connect(self._select_app)
            row.set_selected(stat.app.key == self._current)
            self._app_box.addWidget(row)
            self._app_rows.append(row)

        self.lbl_left.setText(f"{len(self._stats)} 个软件" if self._stats else "暂无")
        self._update_sum()

    def _update_sum(self) -> None:
        total = sum(stat.size for stat in self._stats)
        self.lbl_sum.setText(f"合计 {fmt_bytes(total)}" if self._stats else "合计 —")
        self.lbl_sum.setStyleSheet(
            f"color: {T.TEXT if total else T.TEXT_DIM};"
            f" background: transparent; border: none;"
        )

    def _select_app(self, key: str) -> None:
        self._current = key
        for row in self._app_rows:
            row.set_selected(row.app.key == key)
        self._render_detail()

    def _current_stat(self) -> appclean.AppStat | None:
        for stat in self._stats:
            if stat.app.key == self._current:
                return stat
        return None

    def _render_detail(self) -> None:
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()
        for row in self._path_rows:
            row.setParent(None)
            row.deleteLater()
        self._path_rows.clear()

        stat = self._current_stat()
        if stat is None:
            self.lbl_app.setText("软件专清")
            self.lbl_kinds.setText("")
            self.lbl_total.setText("—")
            self.lbl_total_note.setText("")
            self.bar.hide()
            self._path_title.hide()
            self._sync_buttons()
            return

        self.lbl_app.setText(stat.app.name)
        self.lbl_kinds.setText(f"{stat.kinds} 类 · {stat.count:,} 个文件")
        self.lbl_total.setText(fmt_bytes(stat.size))
        self.lbl_total_note.setText("可清理" if stat.size else "没有可清理的内容")
        self.lbl_hint.setText(stat.app.hint)

        segments = []
        for item in stat.items:
            if item.size > 0:
                segments.append(
                    (float(item.size), KIND_COLORS.get(item.target.kind, T.ACCENT))
                )
        self.bar.set_segments(segments)
        self.bar.setVisible(bool(segments))

        for item in stat.items:
            row = KindRow(item, item.target.key in self._checked)
            row.check.toggled.connect(
                lambda checked, key=item.target.key: self._toggle(key, checked)
            )
            self._kind_box.addWidget(row)
            self._rows.append(row)

        self._render_paths(stat)
        self._sync_buttons()

    def _render_paths(self, stat: appclean.AppStat) -> None:
        """列出当前软件实际会被清理的目录，最多 3 条。"""
        paths: list[str] = []
        for item in stat.items:
            for path in item.target.paths:
                if path not in paths:
                    paths.append(path)

        shown = paths[:4]
        for path in shown:
            row = label("· " + _short_path(path), 10, T.TEXT_DIM)
            row.setToolTip(path)
            self._path_box.addWidget(row)
            self._path_rows.append(row)
        if len(paths) > len(shown):
            more = label(f"· 等 {len(paths)} 处（含多账号目录）", 10, T.TEXT_DIM)
            self._path_box.addWidget(more)
            self._path_rows.append(more)
        self._path_title.setVisible(bool(paths))

    def _toggle(self, key: str, checked: bool) -> None:
        if checked:
            self._checked.add(key)
        else:
            self._checked.discard(key)
        self._sync_buttons()

    def _selected_targets(self) -> list[appclean.Target]:
        """按软件顺序返回勾选的类目。

        不直接遍历 `_checked`：set 无序，确认框里列出来的顺序会每次都不一样。
        """
        out: list[appclean.Target] = []
        for stat in self._stats:
            for item in stat.items:
                if item.target.key in self._checked and item.count:
                    out.append(item.target)
        return out

    def _selected_size(self) -> int:
        size = 0
        for key in self._checked:
            item = self._find_target(key)
            if item is not None:
                size += item.size
        return size

    def _sync_buttons(self) -> None:
        busy = self._phase in ("scanning", "cleaning")
        count = len(self._selected_targets())
        size = self._selected_size()

        self.btn_scan.setText("取消" if self._phase == "scanning" else "重新扫描")
        self.btn_scan.setEnabled(self._phase != "cleaning")
        self.btn_clean.setEnabled(bool(count) and not busy)
        self.btn_clean.setText("清理中…" if self._phase == "cleaning" else "清理选中")
        self.lbl_pick.setText(
            f"已选 {count} 类 · {fmt_bytes(size)}" if count else "已选 0 项"
        )
        self.lbl_pick.setStyleSheet(
            f"color: {T.TEXT if count else T.TEXT_SUB};"
            f" background: transparent; border: none;"
        )

    # ---------------------------------------------------------- 清理

    def _on_clean(self) -> None:
        if self._phase == "cleaning":
            return
        targets = self._selected_targets()
        if not targets:
            return
        size = self._selected_size()

        apps: dict[str, list[str]] = {}
        keys = {target.key for target in targets}
        for stat in self._stats:
            for item in stat.items:
                if item.target.key in keys:
                    apps.setdefault(stat.app.name, []).append(item.target.name)

        lines = [f"将清理 {len(targets)} 类内容，约 {fmt_bytes(size)}：", ""]
        for name, kinds in apps.items():
            lines.append(f"  {name}：{'、'.join(kinds)}")
        lines += [
            "",
            "删掉的是缓存和已经收到的图片/视频/文件，程序会按需重新下载。",
            "聊天记录数据库不在清理范围内；重要文件请先另存。",
        ]

        box = QMessageBox(self)
        box.setWindowTitle("清理软件缓存")
        box.setText("\n".join(lines))
        box.setIcon(QMessageBox.Question)
        box.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        box.button(QMessageBox.Ok).setText("清理")
        box.button(QMessageBox.Cancel).setText("取消")
        if box.exec() != QMessageBox.Ok:
            return

        self._purge_keys = {target.key for target in targets}
        self._phase = "cleaning"
        self.progress.set_value(0, T.ACCENT)
        self.progress.show()
        self.lbl_state.setText("正在删除…")
        self._sync_buttons()
        self.tasks.submit(
            "appclean_purge", "清理软件缓存", self._make_purge_task(targets)
        )

    def _make_purge_task(self, targets: list[appclean.Target]):
        def run(report, should_stop):
            return appclean.purge(
                targets,
                should_stop=should_stop,
                on_progress=lambda percent, text: report(percent, text),
            )

        return run

    def _apply_purge(self, result) -> None:
        self._phase = "done"
        self.progress.hide()
        if result is None:
            self.lbl_state.setText("清理失败，请稍后重试")
            self._sync_buttons()
            return

        for stat in self._stats:
            for item in stat.items:
                if item.target.key in self._purge_keys:
                    item.size = 0
                    item.count = 0
        self._checked -= self._purge_keys
        self._purge_keys = set()

        # 垃圾清理页里同一批文件可能还挂着：按路径把它们摘掉，否则那边
        # 会显示一个早就不存在的体积，点「立即清理」却清了个寂寞
        if result.removed_paths:
            junk = self.store.junk
            if junk.ready:
                keep = [i for i in junk.items if i.path not in result.removed_paths]
                if len(keep) != len(junk.items):
                    self.store.set_junk_items(
                        keep, sum(i.size for i in keep), junk.update_backlog, junk.social
                    )

        freed = fmt_bytes(result.freed)
        if result.remaining:
            self.lbl_state.setText(
                f"已释放 {freed}，还有 {result.remaining} 个文件被程序占用没删掉"
                "（关掉对应软件后再清理一次）"
            )
        else:
            self.lbl_state.setText(f"已释放 {freed} · {result.deleted:,} 个文件")

        biggest = max((stat.size for stat in self._stats), default=0) or 1
        for row, stat in zip(self._app_rows, self._stats):
            row.set_stat(stat.size, stat.count, stat.kinds, stat.size / biggest)
        self._update_sum()

        self._render_detail()
        self._sync_buttons()

    def shutdown(self) -> None:
        self.tasks.cancel_many("appclean_scan", "appclean_purge")
