"""系统瘦身：关掉用不上的系统功能，收回能安全释放的空间。

这一页和「垃圾清理」不重叠：那边删缓存文件，这边改系统行为（更新策略、
遥测服务、推荐位），外加系统自己生成的大文件（休眠文件、转储、旧系统）。

排版用两列而不是一列长列表：13 行开关排成一列要滚两屏，而开关类的东西
最怕「看到一半不知道下面还有」。两列后 720 高的窗口基本一屏装得下，
矮窗口才滚。分组顺序按「影响面」排：更新 → 后台 → 推荐 → 空间。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core import slim, winmem
from core.metrics import fmt_bytes

from . import theme_dark as T
from .components import Card, ToggleSwitch, font, label


def _tag(text: str, color: str) -> QLabel:
    """行内小标签：需管理员 / 不可逆。"""
    tag = QLabel(text)
    tag.setFont(font(9))
    tag.setStyleSheet(
        f"color: {color}; border: 1px solid {T.BORDER}; border-radius: 3px;"
        f" padding: 0 4px; background: transparent;"
    )
    return tag


class _Row(QWidget):
    """带悬停底色的一行。行数多，没有悬停反馈会分不清在指哪一行。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover = False
        self.setAttribute(Qt.WA_Hover, True)
        # 不要设 Maximum：说明文字是 wordWrap 的，高度得靠 heightForWidth 算。
        # 封顶到 sizeHint 会把第三行直接裁掉（截图里半句话就断在那了）。

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        if self._hover:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(T.HOVER))
            painter.drawRoundedRect(self.rect().adjusted(0, 1, 0, -1), 6, 6)
            painter.end()
        super().paintEvent(event)


class ToggleRow(_Row):
    """一个开关：名称 + 影响说明 + 开关。"""

    def __init__(self, item: slim.Toggle, on: bool, parent=None):
        super().__init__(parent)
        self.item = item

        box = QHBoxLayout(self)
        box.setContentsMargins(8, 4, 8, 4)
        box.setSpacing(9)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        head.addWidget(label(item.name, 12, T.TEXT))
        if item.admin:
            head.addWidget(_tag("需管理员", T.TEXT_DIM))
        head.addStretch(1)
        text.addLayout(head)

        self.lbl_effect = label(item.effect, 10, T.TEXT_DIM)
        self.lbl_effect.setWordWrap(True)
        text.addWidget(self.lbl_effect)
        box.addLayout(text, 1)

        # 40px 是量出来的下限：9px 字号配 32px 会把「已」裁成半个字
        # （右对齐时从左边裁），再宽又会把说明文字挤折行
        self.lbl_state = label("", 10, T.TEXT_DIM)
        self.lbl_state.setFixedWidth(40)
        self.lbl_state.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        box.addWidget(self.lbl_state, 0)

        self.switch = ToggleSwitch()
        self.switch.set_checked_silently(on)
        box.addWidget(self.switch, 0, Qt.AlignVCenter)

        self.set_state(on)

    def set_state(self, on: bool, note: str = "") -> None:
        self.switch.set_checked_silently(on)
        if note:
            self.lbl_state.setText(note)
            self.lbl_state.setStyleSheet(
                f"color: {T.RED}; background: transparent; border: none;"
            )
            return
        text = "已开启" if on else "已关闭"
        color = T.TEXT_DIM if on else T.AMBER
        self.lbl_state.setText(text)
        self.lbl_state.setStyleSheet(
            f"color: {color}; background: transparent; border: none;"
        )


class SpaceRow(_Row):
    """一个空间项：名称 + 说明 + 体积 + 动作按钮。"""

    def __init__(self, item: slim.Space, size: int, parent=None):
        super().__init__(parent)
        self.item = item
        self._size = size
        self._busy = False

        box = QHBoxLayout(self)
        box.setContentsMargins(8, 4, 8, 4)
        box.setSpacing(9)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        head.addWidget(label(item.name, 12, T.TEXT))
        if item.danger:
            head.addWidget(_tag("不可逆", T.AMBER))
        head.addStretch(1)
        text.addLayout(head)

        effect = label(item.effect, 10, T.TEXT_DIM)
        effect.setWordWrap(True)
        text.addWidget(effect)
        box.addLayout(text, 1)

        self.lbl_size = label("", 12, T.TEXT)
        self.lbl_size.setFixedWidth(84)
        self.lbl_size.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        box.addWidget(self.lbl_size, 0)

        self.btn = QPushButton(item.action)
        self.btn.setObjectName("Ghost")
        self.btn.setFixedHeight(28)
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setMinimumWidth(72)
        box.addWidget(self.btn, 0, Qt.AlignVCenter)

        self.refresh()

    @property
    def size(self) -> int:
        return self._size

    def refresh(self) -> None:
        """按最新状态重画：体积、按钮文案、能不能点。"""
        size = self._size
        if not self.item.known_size:
            self.lbl_size.setText("由系统决定")
        elif size < 0:
            self.lbl_size.setText("统计中")
        else:
            self.lbl_size.setText(fmt_bytes(size))
        done = self.item.known_size and size == 0
        color = T.TEXT_DIM if done else T.TEXT
        self.lbl_size.setStyleSheet(
            f"color: {color}; background: transparent; border: none;"
        )

        if self._busy:
            self.btn.setText("处理中…")
            self.btn.setEnabled(False)
            return

        if self.item.enabled is None:
            self.btn.setEnabled(not done)
            self.btn.setText("已清理" if done else self.item.action)
            return

        # 可逆项（休眠）：关掉之后按钮变成「重新开启」
        on = self.item.enabled()
        self.btn.setText(self.item.action if on else self.item.restore_text)
        self.btn.setEnabled(True)
        if not on:
            self.lbl_size.setText("已关闭")
            self.lbl_size.setStyleSheet(
                f"color: {T.TEXT_DIM}; background: transparent; border: none;"
            )

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.refresh()

    def set_size(self, size: int) -> None:
        self._size = size
        self.refresh()


class SlimPage(QWidget):
    # 需要向主窗口借的两个能力：toast 与提权重启
    message = Signal(str)
    elevate_requested = Signal()

    def __init__(self, tasks, parent=None):
        super().__init__(parent)
        self.tasks = tasks
        self._rows: dict[str, ToggleRow] = {}
        self._spaces: dict[str, SpaceRow] = {}
        self._groups: dict[str, QLabel] = {}
        self._pending_space = ""

        self.tasks.finished.connect(self._on_task_finished)
        self.tasks.cancelled.connect(self._on_task_cancelled)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # ------------------------------------------------------------ 工具条
        # 页面大标题由主窗口的页头负责，这里只放状态和动作 ——
        # 再画一遍标题会和窗口标题重复成两行「系统瘦身」。
        bar_row = QHBoxLayout()
        bar_row.setSpacing(8)
        self.lbl_stat = label("正在读取…", 11, T.TEXT_DIM)
        bar_row.addWidget(self.lbl_stat, 1)

        self.btn_reset = QPushButton("恢复默认")
        self.btn_reset.setObjectName("Ghost")
        self.btn_reset.setFixedHeight(28)
        self.btn_reset.setCursor(Qt.PointingHandCursor)
        self.btn_reset.clicked.connect(self._on_reset)
        bar_row.addWidget(self.btn_reset, 0)
        root.addLayout(bar_row)

        # ------------------------------------------------------------ 提权提示
        self.admin_bar = QWidget()
        bar = QHBoxLayout(self.admin_bar)
        bar.setContentsMargins(10, 6, 10, 6)
        bar.setSpacing(8)
        self.admin_bar.setStyleSheet(
            f"background: {T.PANEL_SUNKEN}; border: 1px solid {T.BORDER};"
            f" border-radius: 6px;"
        )
        warn = label(
            "当前是普通权限，标有「需管理员」的项目要提权后才能修改。", 10, T.TEXT_SUB
        )
        bar.addWidget(warn, 1)
        btn_el = QPushButton("以管理员身份重启")
        btn_el.setObjectName("Link")
        btn_el.setCursor(Qt.PointingHandCursor)
        btn_el.clicked.connect(self.elevate_requested.emit)
        bar.addWidget(btn_el, 0)
        self.admin_bar.setVisible(not winmem.is_admin())
        root.addWidget(self.admin_bar)

        # ------------------------------------------------------------ 两列
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        root.addWidget(scroll, 1)

        body = QWidget()
        scroll.setWidget(body)
        outer = QVBoxLayout(body)
        outer.setContentsMargins(0, 0, 8, 0)
        outer.setSpacing(7)

        grid = QHBoxLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(9)

        states = slim.states()
        columns: list[QVBoxLayout] = []
        for _ in range(2):
            column = QVBoxLayout()
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(7)
            columns.append(column)
            grid.addLayout(column, 1)

        # 按「实测高度」分列而不是按顺序对半：左列 更新(3) + 空间(2)，
        # 右列 后台(4) + 推荐(2)。空间卡的行带体积和按钮、文字更窄更高，
        # 换到左边正好把左列的余高用掉，两列底部也就齐了
        placement = {"update": 0, "service": 1, "noise": 1, "space": 0}
        for key, title, desc in slim.GROUPS:
            columns[placement[key]].addWidget(
                self._build_group(key, title, desc, states)
            )
        for column in columns:
            column.addStretch(1)
        outer.addLayout(grid, 1)

        # 声明边界。用户看到「系统瘦身」会先担心「是不是把杀毒也关了」，
        # 与其等他翻完整个列表，不如直接把没动的东西写出来。
        note = label(
            "没有动 Windows Defender、防火墙、系统还原和页面文件 —— "
            "瘦身省的是用不上的后台与推荐，不是安全。每一项改之前是什么值，"
            "点「恢复默认」都能改回去。",
            10,
            T.TEXT_DIM,
        )
        note.setWordWrap(True)
        outer.addWidget(note)

        self._refresh_summary(states)

    # ---------------------------------------------------------- 构造

    def _build_group(
        self, key: str, title: str, desc: str, states: dict[str, bool]
    ) -> Card:
        card = Card(padding=12, spacing=6)

        head = QHBoxLayout()
        head.setContentsMargins(6, 0, 6, 0)
        head.setSpacing(6)
        head.addWidget(label(title, 13, T.TEXT, QFont.DemiBold))
        head.addStretch(1)
        count = label("", 10, T.TEXT_DIM)
        head.addWidget(count)
        self._groups[key] = count
        card.body().addLayout(head)

        if key == "space":
            desc += "系统里没有的项不会显示。"
        hint = label(desc, 10, T.TEXT_DIM)
        hint.setWordWrap(True)
        card.body().addWidget(hint)

        line = QLabel()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {T.BORDER_SOFT}; border: none;")
        card.body().addWidget(line)

        if key == "space":
            for item in slim.SPACES:
                row = SpaceRow(item, -1)
                row.btn.clicked.connect(
                    lambda _checked=False, target=row: self._on_space(target)
                )
                card.body().addWidget(row)
                self._spaces[item.key] = row
            return card

        for item in slim.TOGGLES:
            if item.group != key:
                continue
            on = states.get(item.key, True)
            row = ToggleRow(item, on)
            row.switch.toggled.connect(
                lambda checked, target=row: self._on_toggle(target, checked)
            )
            card.body().addWidget(row)
            self._rows[item.key] = row

        return card

    # ---------------------------------------------------------- 开关

    def _on_toggle(self, row: ToggleRow, checked: bool) -> None:
        item = row.item
        if item.admin and not winmem.is_admin():
            row.set_state(not checked, "需权限")
            self.message.emit(f"「{item.name}」需要管理员权限，点上方「以管理员身份重启」")
            return

        error = slim.apply(item.key, checked)
        if error:
            row.set_state(not checked, "失败")
            row.setToolTip(error)
            self.message.emit(f"{item.name} 修改失败：{error}")
            return

        row.set_state(checked)
        row.setToolTip("")
        self._refresh_summary()

    def _on_reset(self) -> None:
        off = [row for row in self._rows.values() if not row.switch.isChecked()]
        if not off:
            self.message.emit("所有项目都已经是系统默认状态")
            return
        answer = QMessageBox.question(
            self,
            "恢复默认",
            f"把已关闭的 {len(off)} 个项目恢复为 Windows 默认设置？\n"
            "只改回系统默认值，不会删除任何文件。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        failed = 0
        for row in off:
            if row.item.admin and not winmem.is_admin():
                failed += 1
                continue
            if slim.apply(row.item.key, True):
                failed += 1
                continue
            row.set_state(True)
        self._refresh_summary()
        self.message.emit(
            "已恢复为系统默认"
            if not failed
            else f"已恢复，{failed} 项因缺少管理员权限未生效"
        )

    # ---------------------------------------------------------- 空间

    def start_scan(self) -> None:
        """统计体积。Windows.old 可能有好几万个文件，放到任务线程里数。"""
        if self.tasks.is_busy("slim_scan"):
            return
        self.lbl_stat.setText("正在统计可回收空间…")
        self.tasks.submit("slim_scan", "统计系统可回收空间", self._scan_task)

    @staticmethod
    def _scan_task(report, should_stop):
        report(10, "统计休眠文件与转储…")
        return slim.scan()

    def _on_space(self, row: SpaceRow) -> None:
        item = row.item
        if item.admin and not winmem.is_admin():
            self.message.emit(f"「{item.name}」需要管理员权限才能处理")
            self.elevate_requested.emit()
            return

        # 可逆项：已经关掉了，这次是重新打开
        if item.enabled is not None and not item.enabled():
            row.set_busy(True)
            try:
                item.restore()
            except OSError as exc:
                row.set_busy(False)
                self.message.emit(f"重新开启失败：{exc}")
                return
            row.set_busy(False)
            row.set_size(item.measure())
            self.message.emit(f"已重新开启{item.name}")
            return

        if item.danger:
            answer = QMessageBox.warning(
                self,
                item.name,
                f"{item.confirm}\n\n继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        self._pending_space = item.key
        row.set_busy(True)
        self.lbl_stat.setText(f"正在处理：{item.name}…")
        self.tasks.submit(
            "slim_space",
            f"处理{item.name}",
            lambda report, should_stop, key=item.key: slim.clear(key),
        )

    def _on_task_finished(self, task_id: str, result) -> None:
        if task_id == "slim_scan":
            sizes = result or {}
            for key, row in self._spaces.items():
                size = sizes.get(key, 0)
                row.set_size(size)
                # 没有残留的项直接收起来：一行「0 B · 已清理」只是占地方。
                # 可逆项（休眠）关掉后体积也是 0，但得留着让人重新打开。
                row.setVisible(
                    size > 0 or not row.item.known_size or row.item.enabled is not None
                )
            self._refresh_summary()
            return

        if task_id == "slim_space":
            freed, text = result or (0, "处理结束")
            row = self._spaces.get(self._pending_space)
            if row is not None:
                row.set_busy(False)
                row.set_size(row.item.measure())
            self._pending_space = ""
            self.message.emit(text if freed <= 0 else f"已释放 {fmt_bytes(freed)} · {text}")
            # 清完重新统计：休眠文件删了以后体积要归零，Windows.old 也不再出现在列表里
            self.start_scan()

    def _on_task_cancelled(self, task_id: str) -> None:
        if task_id == "slim_space":
            row = self._spaces.get(self._pending_space)
            if row is not None:
                row.set_busy(False)
            self._pending_space = ""
            self.message.emit("已取消")
        if task_id == "slim_scan":
            self._refresh_summary()

    # ---------------------------------------------------------- 汇总

    def _refresh_summary(self, states: dict[str, bool] | None = None) -> None:
        states = states or {key: row.switch.isChecked() for key, row in self._rows.items()}
        off = sum(1 for on in states.values() if not on)
        sizes = sum(
            row.size for row in self._spaces.values() if row.item.known_size and row.size > 0
        )
        # 两种状态都用一个句子：扫完只写「可回收 X」的话，点一下开关文案
        # 就跳到另一套格式，看起来像是两块不同的信息
        parts = [f"已关闭 {off} / {len(states)} 项"]
        parts.append(f"可回收 {fmt_bytes(sizes)}" if sizes > 0 else "没有可回收的空间")
        self.lbl_stat.setText(" · ".join(parts))

        for key, lbl in self._groups.items():
            items = [item for item in slim.TOGGLES if item.group == key]
            if not items:
                continue
            closed = sum(1 for item in items if not states.get(item.key, True))
            lbl.setText(f"{closed} / {len(items)} 已关闭" if closed else "")
