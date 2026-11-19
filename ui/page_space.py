"""磁盘空间页：回答「C 盘空间去哪了」。

大文件列表回答的是「哪几个文件大」，可它解决不了单分区机器的真问题：
`Windows\\Installer` 三万个碎文件、`AppData\\Local\\Packages` 每个几百 MB，
按文件列出来一条都看不见。所以这一页的核心是**目录聚合**。

三段布局（见 docs/C盘扫描加强方案.md）：

1. 空间去向：一条堆叠条，系统 / 应用 / 用户 / 系统文件 / 可用
2. 目录占用：可以从 C:\\ 一路点下去，每一层都是「子目录 + 体积 + 占比」
3. 可回收：把休眠文件、还原点、组件存储这些大块占用按体积排开

扫描是渐进的：每扫完一个顶层目录就先把这一行显示出来（`tasks.partial`），
不用等全盘两分钟。
"""

from __future__ import annotations

import os
import time

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core import dirstat, space, spacehistory, spacereport
from core.winmem import fmt_size

from . import theme_dark as T
from .components import (
    Card,
    HoverMixin,
    Segmented,
    copy_text,
    label,
    open_folder,
    reveal_file,
    run_system,
)
from .page_bigfile import BigFilePage

SEGMENTS = [("analysis", "空间分析"), ("files", "大文件")]
SEGMENT_SUBS = {
    "analysis": "看清系统盘每个目录占多少、哪些能回收",
    "files": "按类型扫视频、安装包等大文件，勾选后送进回收站",
}

MAX_DIR_ROWS = 11
MAX_RECLAIM_ROWS = 8


def _cat_color(key: str) -> str:
    return {
        "system": T.BLUE,
        "app": T.PURPLE,
        "user": T.GREEN,
        "system_file": T.AMBER,
        "other": T.TEXT_DIM,
    }.get(key, T.TEXT_DIM)


def _unscan_color() -> str:
    """「还没扫到」那一段：强调色的淡版。

    原来用 BORDER 的灰，和右边「可用」的浅灰几乎同色，扫到一半的条看起来
    像「已用一半、空一半」。淡蓝即表明「在扫」，也和可用区分得开。
    """
    color = QColor(T.ACCENT)
    color.setAlpha(90)
    return color.name(QColor.HexArgb)


def _drive_text() -> str:
    return os.environ.get("SystemDrive", "C:") + "\\"


# ------------------------------------------------------------------ 小控件


class ThinBar(QWidget):
    """扫描进度。3px 高的一条，扫描时出现，结束就藏起来。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ratio = 0.0
        self.setFixedHeight(3)
        self.hide()

    def set_ratio(self, ratio: float) -> None:
        ratio = max(0.0, min(1.0, ratio))
        if abs(ratio - self._ratio) < 0.005:
            return
        self._ratio = ratio
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(T.PANEL_SUNKEN))
        painter.drawRoundedRect(QRectF(self.rect()), 1.5, 1.5)
        if self._ratio > 0:
            width = self.width() * self._ratio
            painter.setBrush(QColor(T.ACCENT))
            painter.drawRoundedRect(QRectF(0, 0, width, self.height()), 1.5, 1.5)
        painter.end()


class StackBar(QWidget):
    """空间去向的堆叠条。段与段之间留 2px，色块才不会糊成一片。"""

    GAP = 2.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._parts: list[tuple[int, str]] = []
        self.setFixedHeight(14)

    def set_parts(self, parts: list[tuple[int, str]]) -> None:
        self._parts = [(size, color) for size, color in parts if size > 0]
        self.update()

    def paintEvent(self, event) -> None:
        if not self._parts:
            return
        total = sum(size for size, _ in self._parts)
        if total <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)

        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 4, 4)
        painter.setClipPath(path)

        x = 0.0
        width = float(self.width())
        last = len(self._parts) - 1
        for index, (size, color) in enumerate(self._parts):
            span = width * size / total
            # 最后一段吃掉累积误差，右边缘才不会留一条缝
            draw_to = width if index == last else x + max(span - self.GAP, 1.0)
            painter.setBrush(QColor(color))
            painter.drawRect(QRectF(x, 0, max(draw_to - x, 1.0), self.height()))
            x = draw_to
        painter.end()


class RatioBar(QWidget):
    """行内占比条。"""

    def __init__(self, width: int = 86, parent=None):
        super().__init__(parent)
        self._ratio = 0.0
        self._color = T.ACCENT
        self.setFixedSize(width, 5)

    def set_value(self, ratio: float, color: str) -> None:
        ratio = max(0.0, min(1.0, ratio))
        if abs(ratio - self._ratio) < 0.004 and color == self._color:
            return
        self._ratio = ratio
        self._color = color
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        # 轨道别用 PANEL_SUNKEN：它和行悬停底色几乎同色，整行一亮起
        # 蓝色填充就像突然短了一截
        painter.setBrush(QColor(T.BORDER_SOFT))
        painter.drawRoundedRect(QRectF(self.rect()), 2.5, 2.5)
        if self._ratio > 0:
            width = max(2.0, self.width() * self._ratio)
            painter.setBrush(QColor(self._color))
            painter.drawRoundedRect(QRectF(0, 0, width, self.height()), 2.5, 2.5)
        painter.end()


class DirRow(HoverMixin, QWidget):
    """目录占用的一行：名称 + 占比条 + 体积。整行可点，点进去看下一层。"""

    clicked = Signal(object)
    menu_requested = Signal(object, object)

    HEIGHT = 26

    def __init__(self, parent=None):
        super().__init__(parent)
        self.node: dirstat.Node | None = None
        self.setFixedHeight(self.HEIGHT)
        self.setAttribute(Qt.WA_Hover, True)
        self.setCursor(Qt.PointingHandCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(6, 0, 6, 0)
        row.setSpacing(8)

        self.lbl_name = label("", 12, T.TEXT)
        row.addWidget(self.lbl_name, 1)

        self.bar = RatioBar()
        row.addWidget(self.bar, 0)

        self.lbl_size = label("", 12, T.TEXT_SUB)
        self.lbl_size.setFixedWidth(66)
        self.lbl_size.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.lbl_size, 0)

        # 占整盘的比例：条形是「占父级」，两者一起看才不会误读 ——
        # 只看条形的话，点进任何一个目录都有一条撑满的，像是又发现了一块大户
        self.lbl_pct = label("", 11, T.TEXT_DIM)
        self.lbl_pct.setFixedWidth(38)
        self.lbl_pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.lbl_pct, 0)

    def set_node(self, node: dirstat.Node, ratio: float, pct: float = 0.0) -> None:
        self.node = node
        mark = "▸ " if node.is_dir and (node.children or node.folded) else "  "
        self.lbl_name.setText(f"{mark}{node.name}")
        self.lbl_size.setText(fmt_size(node.size))
        self.lbl_pct.setText(f"{pct:.0f}%" if pct >= 0.5 else "<1%")
        self.bar.set_value(ratio, _cat_color(dirstat.category_of(node)))
        tip = [node.path, dirstat.describe(node), f"占整盘 {pct:.1f}%"]
        if node.folded:
            tip.append(f"另有 {node.folded} 个子目录 {fmt_size(node.folded_size)}")
        self.setToolTip("\n".join(tip))

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            if self.node is not None:
                self.clicked.emit(self.node)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        if self.node is not None:
            self.menu_requested.emit(self.node, event.globalPos())

    def paintEvent(self, event) -> None:
        if self._hover:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(T.HOVER))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(0, 1, 0, -1), 5, 5)
            painter.end()


class ReclaimRow(QWidget):
    """可回收的一项：名称 + 风险标签 + 体积 + 动作按钮。"""

    clicked = Signal(object)

    HEIGHT = 30

    def __init__(self, parent=None):
        super().__init__(parent)
        self.item: spacereport.Reclaim | None = None
        self.setFixedHeight(self.HEIGHT)

        row = QHBoxLayout(self)
        row.setContentsMargins(6, 0, 2, 0)
        row.setSpacing(6)

        # 名称在前、风险标签紧跟其后、中间留空、体积与按钮靠右。
        # 原来标签排在名称和体积之间，位置随名称长度漂移，四行看下来参差不齐。
        self.lbl_name = label("", 12, T.TEXT)
        row.addWidget(self.lbl_name, 0)

        self.lbl_risk = label("", 10, T.RED)
        row.addWidget(self.lbl_risk, 0)

        row.addStretch(1)

        self.lbl_size = label("", 12, T.TEXT)
        self.lbl_size.setFixedWidth(62)
        self.lbl_size.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.lbl_size, 0)

        self.btn = QPushButton("")
        self.btn.setObjectName("Ghost")
        self.btn.setFixedHeight(22)
        self.btn.setFixedWidth(58)
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.clicked.connect(self._fire)
        row.addWidget(self.btn, 0)

    def set_item(self, item: spacereport.Reclaim, action_text: str) -> None:
        self.item = item
        self.lbl_name.setText(item.name)
        self.lbl_risk.setText(item.risk)
        self.lbl_size.setText(fmt_size(item.size))
        self.btn.setText(action_text)
        self.btn.setToolTip(item.note)
        self.setToolTip(item.note)

    def _fire(self) -> None:
        if self.item is not None:
            self.clicked.emit(self.item)


class SpaceAnalysisView(QWidget):
    """空间分析本体。"""

    def __init__(self, store, tasks, on_navigate=None, parent=None):
        super().__init__(parent)
        self.store = store
        self.tasks = tasks
        self._nav = on_navigate or (lambda _key: None)
        self._result: space.SpaceResult | None = None
        self._live: dirstat.Node | None = None      # 扫描中的临时树
        self._stack: list[dirstat.Node] = []
        self._busy = False
        self._used = 0        # 整盘已用。不等扫描：进页面先量一次
        self._total = 0       # 整盘容量，堆叠条与「占盘」列共用的分母
        self._quick: list[spacereport.Reclaim] = []   # 扫描中先摆出来的可回收项
        self._refresh_key = ""                        # 正在复测的那一项

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # ---------------- 工具行
        head = QHBoxLayout()
        head.setSpacing(8)
        head.setContentsMargins(2, 0, 2, 0)

        self.lbl_where = label(_drive_text(), 15, T.TEXT, QFont.DemiBold)
        head.addWidget(self.lbl_where, 0, Qt.AlignVCenter)
        self.lbl_info = label("尚未扫描", 11, T.TEXT_DIM)
        head.addWidget(self.lbl_info, 1, Qt.AlignVCenter)

        self.btn_deep = QPushButton("深入分析")
        self.btn_deep.setObjectName("Ghost")
        self.btn_deep.setFixedHeight(26)
        self.btn_deep.setToolTip(
            "额外读系统还原点与组件存储可回收量。DISM 要跑一分钟左右，所以不自动做。"
        )
        self.btn_deep.clicked.connect(lambda: self.start_scan(deep=True))
        head.addWidget(self.btn_deep, 0)

        self.btn_scan = QPushButton("重新扫描")
        self.btn_scan.setFixedHeight(26)
        self.btn_scan.clicked.connect(self._on_scan_clicked)
        head.addWidget(self.btn_scan, 0)
        root.addLayout(head)

        self.progress = ThinBar()
        root.addWidget(self.progress)

        # ---------------- 空间去向
        self.card_bar = Card(padding=12, spacing=9)
        title = QHBoxLayout()
        title.setContentsMargins(0, 0, 0, 0)
        title.addWidget(label("空间去向", 12, T.TEXT, QFont.DemiBold), 0)
        title.addStretch(1)
        self.lbl_bar_note = label("", 10, T.TEXT_DIM)
        title.addWidget(self.lbl_bar_note, 0)
        self.card_bar.body().addLayout(title)

        # 结论先给一句，数据在后：用户要的是「满没满、谁最大、能腾多少」，
        # 不是先看一条彩色条自己推
        self.lbl_verdict = label("", 11, T.TEXT_SUB)
        self.lbl_verdict.setWordWrap(True)
        self.card_bar.add(self.lbl_verdict)

        self.bar = StackBar()
        self.card_bar.add(self.bar)

        self.legend = QHBoxLayout()
        self.legend.setContentsMargins(0, 0, 0, 0)
        self.legend.setSpacing(14)
        self._dots: dict[str, object] = {}
        for key in ("system", "app", "user", "system_file", "other"):
            dot = QWidget()
            dot.setFixedSize(8, 8)
            dot.setStyleSheet(
                f"background: {_cat_color(key)}; border-radius: 2px;"
            )
            cell = QHBoxLayout()
            cell.setContentsMargins(0, 0, 0, 0)
            cell.setSpacing(5)
            cell.addWidget(dot, 0)
            name = label(dirstat.CATEGORY_LABEL[key], 11, T.TEXT_DIM)
            value = label("—", 11, T.TEXT)
            cell.addWidget(name, 0)
            cell.addWidget(value, 0)
            holder = QWidget()
            holder.setLayout(cell)
            self.legend.addWidget(holder, 0)
            self._dots[key] = value

        # 扫描中才出现的「未扫」图例：不给它一个块，用户会以为「其他」那一格
        # 是数据没出来
        self._unscan_holder = QWidget()
        unscan_cell = QHBoxLayout(self._unscan_holder)
        unscan_cell.setContentsMargins(0, 0, 0, 0)
        unscan_cell.setSpacing(5)
        unscan_dot = QWidget()
        unscan_dot.setFixedSize(8, 8)
        unscan_dot.setStyleSheet(
            f"background: {_unscan_color()}; border-radius: 2px;"
        )
        unscan_cell.addWidget(unscan_dot, 0)
        unscan_cell.addWidget(label("未扫", 11, T.TEXT_DIM), 0)
        self.lbl_unscan = label("—", 11, T.TEXT)
        unscan_cell.addWidget(self.lbl_unscan, 0)
        self.legend.addWidget(self._unscan_holder, 0)
        self._unscan_holder.hide()
        cell = QHBoxLayout()
        cell.setContentsMargins(0, 0, 0, 0)
        cell.setSpacing(5)
        free_dot = QWidget()
        free_dot.setFixedSize(8, 8)
        free_dot.setStyleSheet(
            f"background: {T.BORDER_SOFT}; border: 1px solid {T.BORDER}; border-radius: 2px;"
        )
        cell.addWidget(free_dot, 0)
        cell.addWidget(label("可用", 11, T.TEXT_DIM), 0)
        self.lbl_free = label("—", 11, T.TEXT)
        cell.addWidget(self.lbl_free, 0)
        holder = QWidget()
        holder.setLayout(cell)
        self.legend.addWidget(holder, 0)
        self.legend.addStretch(1)
        self.card_bar.body().addLayout(self.legend)
        root.addWidget(self.card_bar)

        # ---------------- 两列
        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(8)

        self.card_dirs = Card(padding=12, spacing=6)
        head_dirs = QHBoxLayout()
        head_dirs.setContentsMargins(0, 0, 0, 0)
        head_dirs.setSpacing(6)
        head_dirs.addWidget(label("目录占用", 12, T.TEXT, QFont.DemiBold), 0)
        # 当前路径紧贴标题：原来它排在 stretch 后面，正好落在「大小」列上方，
        # 看着像这一列的表头
        self.lbl_crumb = label(_drive_text(), 10, T.TEXT_DIM)
        head_dirs.addWidget(self.lbl_crumb, 0)
        head_dirs.addStretch(1)
        self.btn_up = QPushButton("↑ 上级")
        self.btn_up.setObjectName("Ghost")
        self.btn_up.setFixedHeight(20)
        self.btn_up.setFixedWidth(52)
        self.btn_up.setEnabled(False)
        self.btn_up.clicked.connect(self._up)
        head_dirs.addWidget(self.btn_up, 0)
        self.card_dirs.body().addLayout(head_dirs)

        header = QHBoxLayout()
        header.setContentsMargins(6, 0, 6, 0)
        header.setSpacing(8)
        header.addWidget(label("名称", 10, T.TEXT_DIM), 1)
        spacer = QWidget()
        spacer.setFixedWidth(86)
        header.addWidget(spacer, 0)
        size_head = label("大小", 10, T.TEXT_DIM)
        size_head.setFixedWidth(66)
        size_head.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        header.addWidget(size_head, 0)
        pct_head = label("占盘", 10, T.TEXT_DIM)
        pct_head.setFixedWidth(38)
        pct_head.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        header.addWidget(pct_head, 0)
        self.card_dirs.body().addLayout(header)

        self.dir_rows: list[DirRow] = []
        for _ in range(MAX_DIR_ROWS):
            row = DirRow()
            row.clicked.connect(self._enter)
            row.menu_requested.connect(self._dir_menu)
            self.card_dirs.add(row)
            self.dir_rows.append(row)

        self.lbl_more = label("", 10, T.TEXT_DIM)
        self.lbl_more.setContentsMargins(6, 0, 0, 0)
        self.card_dirs.add(self.lbl_more)

        # 目录列表下方这块空白，正好放「近 30 天谁在长」——
        # 单分区用户最常问的其实是「我也没装东西，C 盘怎么又满了」
        hist = QVBoxLayout()
        hist.setContentsMargins(6, 6, 6, 0)
        hist.setSpacing(2)
        head_hist = QHBoxLayout()
        head_hist.setContentsMargins(0, 0, 0, 0)
        self.lbl_hist = label("空间变化", 10, T.TEXT_DIM)
        head_hist.addWidget(self.lbl_hist, 0)
        head_hist.addStretch(1)
        self.lbl_hist_delta = label("", 10, T.TEXT_DIM)
        head_hist.addWidget(self.lbl_hist_delta, 0)
        hist.addLayout(head_hist)
        self.hist_rows: list[QLabel] = []
        for _ in range(3):
            row = label("", 11, T.TEXT_SUB)
            hist.addWidget(row)
            self.hist_rows.append(row)
        self.card_dirs.body().addStretch(1)
        self.card_dirs.body().addLayout(hist)
        columns.addWidget(self.card_dirs, 3)

        self.card_reclaim = Card(padding=12, spacing=6)
        head_rec = QHBoxLayout()
        head_rec.setContentsMargins(0, 0, 0, 0)
        head_rec.addWidget(label("可回收", 12, T.TEXT, QFont.DemiBold), 0)
        head_rec.addStretch(1)
        self.lbl_reclaim_total = label("", 12, T.TEXT)
        head_rec.addWidget(self.lbl_reclaim_total, 0)
        self.card_reclaim.body().addLayout(head_rec)

        self.lbl_reclaim_note = label("按体积排序，动作各自不同", 10, T.TEXT_DIM)
        self.card_reclaim.add(self.lbl_reclaim_note)

        self.rec_rows: list[ReclaimRow] = []
        for _ in range(MAX_RECLAIM_ROWS):
            row = ReclaimRow()
            row.clicked.connect(self._run_action)
            self.card_reclaim.add(row)
            self.rec_rows.append(row)

        # 告诉用户「按下去会发生什么」：这些按钮不是跳转，是就地执行 + 复测
        self.lbl_loop_note = label("", 10, T.TEXT_DIM)
        self.lbl_loop_note.setWordWrap(True)
        self.card_reclaim.add(self.lbl_loop_note)
        self.card_reclaim.body().addStretch(1)

        # 卡片底部这块留白原来空着，正好放「深入分析」的说明：
        # 还原点和组件存储要调 vssadmin / DISM，不能自动跑
        self.lbl_deep_note = label("", 10, T.TEXT_DIM)
        self.lbl_deep_note.setWordWrap(True)
        self.card_reclaim.add(self.lbl_deep_note)
        columns.addWidget(self.card_reclaim, 2)
        root.addLayout(columns, 1)

        # ---------------- 单分区提示
        self.hint = QWidget()
        self.hint.setObjectName("Hint")
        self.hint.setStyleSheet(
            f"#Hint {{ background: {T.PANEL_ALT}; border: 1px solid {T.BORDER};"
            f" border-radius: 8px; }}"
        )
        hint_row = QHBoxLayout(self.hint)
        hint_row.setContentsMargins(12, 7, 10, 7)
        hint_row.setSpacing(8)
        self.lbl_hint = label("", 11, T.TEXT_SUB)
        self.lbl_hint.setWordWrap(True)
        hint_row.addWidget(self.lbl_hint, 1)
        btn_help = QPushButton("怎么办")
        btn_help.setObjectName("Ghost")
        btn_help.setFixedHeight(22)
        btn_help.clicked.connect(self._show_help)
        hint_row.addWidget(btn_help, 0)
        self.hint.hide()
        root.addWidget(self.hint)

        self.store.space_changed.connect(self._on_store_changed)
        self.tasks.progress.connect(self._on_progress)
        self.tasks.partial.connect(self._on_partial)
        self.tasks.finished.connect(self._on_finished)
        self.tasks.cancelled.connect(self._on_finished)
        self.tasks.failed.connect(self._on_failed)

    # ---------------------------------------------------------- 扫描

    def ensure_scan(self) -> None:
        """第一次切进来就自动扫。已经扫过就直接用缓存。"""
        if self._result is not None or self._busy:
            return
        if self.store.space.ready:
            self._adopt(self.store.space.result)
            return
        QTimer.singleShot(0, self.start_scan)

    def start_scan(self, deep: bool = False) -> None:
        if self._busy:
            return
        self._busy = True
        self._live = dirstat.Node(path=_drive_text())
        self._stack.clear()
        self._quick = []
        # 容量 / 已用立刻拿：堆叠条从第一帧就有分母，右侧也先把秒级项摆出来
        self._used, self._total = space.usage()
        self.progress.show()
        self.progress.set_ratio(0.02)
        self.btn_scan.setText("停止")
        self.btn_deep.setEnabled(False)
        self.lbl_info.setText("正在统计目录…")
        self._fill_bar()
        self._fill_verdict()
        self._fill_reclaim()

        self.tasks.submit(
            "space",
            "分析磁盘空间",
            self._make_task(deep),
        )

    def _make_task(self, deep: bool):
        def run(report, should_stop):
            def on_dir(node, done, total):
                # 从任务线程发回主线程，界面收到一行就先画一行
                self.tasks.partial.emit(
                    "space", {"kind": "dir", "node": node, "done": done, "total": total}
                )

            def on_items(items):
                self.tasks.partial.emit("space", {"kind": "items", "items": items})

            return space.run(
                should_stop=should_stop,
                report=report,
                on_dir=on_dir,
                on_items=on_items,
                slow=deep,
            )

        return run

    def _on_scan_clicked(self) -> None:
        if self._busy:
            self.tasks.cancel("space")
            self.lbl_info.setText("正在停止…")
            return
        self.start_scan()

    def _on_progress(self, task_id: str, percent: int, text: str) -> None:
        if task_id != "space":
            return
        self.progress.set_ratio(percent / 100)
        if text:
            self.lbl_info.setText(text)

    def _on_partial(self, task_id: str, payload) -> None:
        if task_id != "space":
            return
        kind = payload.get("kind") if isinstance(payload, dict) else ""
        if kind == "items":
            # 扫描还没结束，秒级那几项先上桌
            self._quick = list(payload.get("items") or [])
            self._fill_reclaim()
            self._fill_verdict()
            return
        if kind != "dir" or self._live is None:
            return
        node, done, total = payload["node"], payload["done"], payload["total"]
        self._live.children.append(node)
        self._live.children.sort(key=lambda n: n.size, reverse=True)
        self._live.size += node.size
        self._live.files += node.files
        self._fill_dirs()
        self._fill_bar()
        self._fill_verdict()
        self.lbl_info.setText(f"已扫 {done}/{total} 个顶层目录 · {node.name}")

    def _on_finished(self, task_id: str, result=None) -> None:
        if task_id == "space_refresh":
            self._apply_refresh(result)
            return
        if task_id == "space_sub":
            self._apply_sub(result)
            return
        if task_id != "space":
            return
        self._busy = False
        self.progress.hide()
        self.btn_scan.setText("重新扫描")
        self.btn_deep.setEnabled(True)
        if result is None:
            self.lbl_info.setText("已停止")
            return
        self._adopt(result)

    def _on_failed(self, task_id: str, _label: str, error: str) -> None:
        if task_id == "space_refresh":
            self._refresh_key = ""
            self.lbl_info.setText(f"重测失败：{error}")
            return
        if task_id not in ("space", "space_sub"):
            return
        self._busy = False
        self.progress.hide()
        self.btn_scan.setText("重新扫描")
        self.btn_deep.setEnabled(True)
        self.lbl_info.setText(f"扫描失败：{error}")

    def _on_store_changed(self) -> None:
        if self.store.space.ready and self._result is None:
            self._adopt(self.store.space.result)

    def _adopt(self, result: space.SpaceResult) -> None:
        self._result = result
        self._live = None
        self._stack.clear()
        self.lbl_info.setText(
            f"已用 {fmt_size(result.used)} / {fmt_size(result.total)}"
            f" · {result.root.files:,} 个文件 · 用时 {result.seconds:.0f} 秒"
        )
        self._fill_all()

    # ---------------------------------------------------------- 渲染

    def _fill_all(self) -> None:
        self._fill_bar()
        self._fill_dirs()
        self._fill_reclaim()
        self._fill_history()
        self._fill_hint()
        self._fill_verdict()

    def _current(self) -> dirstat.Node | None:
        if self._stack:
            return self._stack[-1]
        if self._result is not None:
            return self._result.root
        return self._live

    def _fill_bar(self) -> None:
        node = self._result.root if self._result else self._live
        if node is None:
            return
        used = self._result.used if self._result else self._used
        total = self._result.total if self._result else self._total
        keys = ("system", "app", "user", "system_file", "other")

        scanning = self._result is None and self._busy
        if scanning:
            # 扫描中不能用 summarize()：它会把「还没扫到的部分」塞进「其他」，
            # 于是「其他」越扫越小，最后突然缩水 —— 这里单独画一段中灰。
            parts = {key: 0 for key in keys}
            for child in node.children:
                parts[dirstat.category_of(child)] += child.size
        else:
            parts = dirstat.summarize(node, used)

        chunks: list[tuple[int, str]] = []
        for key in keys:
            value = parts.get(key, 0)
            if value > 0:
                chunks.append((value, _cat_color(key)))
        if scanning and used > node.size:
            chunks.append((used - node.size, _unscan_color()))
            self.lbl_unscan.setText(fmt_size(used - node.size))
            self._unscan_holder.show()
        else:
            self._unscan_holder.hide()
        if total > used:
            chunks.append((total - used, T.BORDER_SOFT))
        self.bar.set_parts(chunks)

        for key, item in self._dots.items():
            value = parts.get(key, 0)
            item.setText(fmt_size(value) if value else "—")
        self.lbl_free.setText(fmt_size(max(0, total - used)) if total else "—")

        if scanning:
            self.lbl_bar_note.setText(
                f"扫描中 · 已扫 {fmt_size(node.size)} / {fmt_size(used)}"
            )
        elif self._result:
            self.lbl_bar_note.setText(
                f"已用 {self._result.percent:.0f}% · 剩余 {fmt_size(self._result.free)}"
            )

    def _fill_dirs(self) -> None:
        node = self._current()
        if node is None:
            for row in self.dir_rows:
                row.hide()
            self.lbl_more.setText("")
            return

        drive = f"{_drive_text()}".rstrip("\\")
        parts = [drive] + [item.name for item in self._stack]
        crumb = "  ›  ".join(parts)
        self.lbl_crumb.setText(crumb if len(crumb) <= 42 else "…" + crumb[-40:])
        self.lbl_crumb.setToolTip(crumb)
        self.btn_up.setEnabled(bool(self._stack))

        parent_total = node.size or 1
        disk = (self._result.total if self._result else self._total) or 0
        children = [child for child in node.children if child.size > 0][:MAX_DIR_ROWS]
        for index, row in enumerate(self.dir_rows):
            if index < len(children):
                child = children[index]
                row.set_node(
                    child,
                    child.size / parent_total,
                    (child.size / disk * 100) if disk else 0.0,
                )
                row.show()
            else:
                row.hide()

        rest = len([c for c in node.children if c.size > 0]) - len(children)
        tail = []
        if rest > 0:
            tail.append(f"另有 {rest} 项")
        if node.folded:
            tail.append(f"{node.folded} 个子目录共 {fmt_size(node.folded_size)}")
        if node.cloud:
            tail.append(f"{fmt_size(node.cloud)} 在云端（不占本地）")
        if node.errors:
            tail.append(f"{node.errors} 项无权限")
        self.lbl_more.setText(" · ".join(tail))

    def _fill_reclaim(self) -> None:
        scanning = self._result is None
        items = self._quick if scanning else list(self._result.items)
        items = sorted(items, key=lambda i: i.size, reverse=True)[:MAX_RECLAIM_ROWS]
        for index, row in enumerate(self.rec_rows):
            if index < len(items):
                item = items[index]
                text = item.verb or ACTION_TEXT.get(item.action, "打开")
                row.set_item(item, text)
                row.show()
            else:
                # 空行要清掉 item：只 hide 的话，隐藏行里还留着上一条的引用，
                # 后续复测 / 统计会把它当成「还在的一项」
                row.item = None
                row.hide()

        if items:
            self.lbl_reclaim_total.setText(fmt_size(spacereport.total(items)))
            note = spacereport.split_note(items)
            if scanning:
                note = "扫描中 · 已列出能立刻算出来的几项"
            self.lbl_reclaim_note.setText(note)
            self.lbl_loop_note.setText(
                "「禁用 / 清理」点下去就执行，完成后自动重测体积。"
                if any(item.cmd for item in items)
                else ""
            )
            deep = any(item.key in ("winsxs", "restore_points") for item in items)
            self.lbl_deep_note.setText(
                "已包含系统还原点与组件存储。"
                if deep
                else "「深入分析」还能查系统还原点与组件存储，要跑一分钟左右。"
            )
        else:
            self.lbl_reclaim_total.setText("")
            self.lbl_loop_note.setText("")
            self.lbl_reclaim_note.setText(
                "正在统计可回收项…" if scanning else "没有发现可以安全回收的大块占用"
            )
            self.lbl_deep_note.setText("")

    def _fill_verdict(self) -> None:
        """一句话结论：满没满、谁最大、能立刻腾多少。"""
        node = self._result.root if self._result else self._live
        if node is None:
            self.lbl_verdict.setText("")
            return
        used = self._result.used if self._result else self._used
        total = self._result.total if self._result else self._total
        if not total:
            self.lbl_verdict.setText("")
            return

        percent = used / total * 100
        parts = [f"C 盘已用 {percent:.0f}%（剩 {fmt_size(max(0, total - used))}）"]

        top = max(node.children, key=lambda n: n.size, default=None)
        if top is not None and top.size > 0:
            parts.append(f"最大是 {top.name} {fmt_size(top.size)}")

        items = (self._quick if self._result is None else self._result.items) or []
        ready = [item for item in items if item.cmd or item.action == "clean"]
        if ready:
            best = max(ready, key=lambda i: i.size)
            name = best.name if best.size else ""
            if len(ready) > 1:
                parts.append(
                    f"可立刻回收 {fmt_size(sum(i.size for i in ready))}"
                    f"（最大一项：{name} {fmt_size(best.size)}）"
                )
            else:
                parts.append(f"可立刻回收 {fmt_size(best.size)}（{name}）")

        self.lbl_verdict.setText("；".join(parts) + "。")

    def _fill_history(self) -> None:
        grows = spacehistory.growth(30, limit=3)
        if not grows:
            self.lbl_hist.setText("空间变化")
            self.lbl_hist_delta.setText("")
            self.hist_rows[0].setText("再扫一次就能看出哪个目录在长")
            for row in self.hist_rows[1:]:
                row.setText("")
            return

        self.lbl_hist.setText(f"近 30 天（{spacehistory.span(30)}）")
        total = sum(g.delta for g in grows)
        self.lbl_hist_delta.setText(
            f"{'+' if total >= 0 else '−'}{fmt_size(abs(total))}"
        )
        self.lbl_hist_delta.setStyleSheet(
            f"color: {T.RED if total >= 0 else T.GREEN}; background: transparent; border: none;"
        )
        for index, row in enumerate(self.hist_rows):
            if index >= len(grows):
                row.setText("")
                continue
            grow = grows[index]
            sign = "+" if grow.up else "−"
            row.setText(f"{grow.name}   {sign}{fmt_size(abs(grow.delta))}")
            row.setStyleSheet(
                f"color: {T.RED if grow.up else T.GREEN}; background: transparent; border: none;"
            )

    def _fill_hint(self) -> None:
        if self._result is None:
            self.hint.hide()
            return
        if not self._result.single_disk:
            self.hint.hide()
            return
        percent = self._result.percent
        if percent < 75:
            self.hint.hide()
            return
        self.lbl_hint.setText(
            f"本机只有一个分区，系统与个人数据共用 C 盘，已用 {percent:.0f}%。"
            "单分区机器没有「搬到别的盘」这条路，只能清理或迁到外接盘。"
        )
        self.hint.show()

    # ---------------------------------------------------------- 交互

    def _enter(self, node: dirstat.Node) -> None:
        if not node.is_dir or self._busy:
            return
        if node.children:
            self._stack.append(node)
            self._fill_dirs()
            return
        # depth 之外的目录还没有子项，现扫一层（单目录，通常几百毫秒）
        if node.size <= 0:
            return
        self.lbl_info.setText(f"正在展开 {node.name}…")
        parent = node

        def run(report, should_stop):
            return dirstat.scan_subdir(parent.path, should_stop=should_stop)

        self.tasks.submit("space_sub", f"展开 {node.name}", run)

    def _apply_sub(self, result) -> None:
        node = self._current()
        if result is None or node is None:
            self.lbl_info.setText("展开失败")
            return
        node.children = result.children
        node.folded = result.folded
        node.folded_size = result.folded_size
        node.errors = result.errors
        self._stack.append(node)
        self._fill_dirs()
        if self._result:
            self.lbl_info.setText(
                f"已用 {fmt_size(self._result.used)} / {fmt_size(self._result.total)}"
                f" · {self._result.root.files:,} 个文件"
            )

    def _up(self) -> None:
        if not self._stack:
            return
        self._stack.pop()
        self._fill_dirs()

    def _dir_menu(self, node: dirstat.Node, position) -> None:
        menu = QMenu(self)
        if node.is_dir:
            menu.addAction("打开目录", lambda: open_folder(node.path))
        else:
            menu.addAction("在资源管理器中显示", lambda: reveal_file(node.path))
        menu.addAction("复制路径", lambda: copy_text(node.path))
        menu.exec(position)

    def _apply_refresh(self, item) -> None:
        """禁用 / 清理之后，把这一项换成重测结果：还在就更新数字，归零就撤行。"""
        key = self._refresh_key
        self._refresh_key = ""
        if not key:
            return
        fresh = item if (item is not None and item.measurable) else None

        def replace(items: list[spacereport.Reclaim]) -> list[spacereport.Reclaim]:
            return [fresh if old.key == key else old for old in items] if fresh else [
                old for old in items if old.key != key
            ]

        self._quick = replace(self._quick)
        if self._result is not None:
            self._result.items = replace(self._result.items)

        # 盘上的数字变了，容量口径也要跟着更新，否则「已用 %」还停在旧值
        self._used, self._total = space.usage()
        self._fill_reclaim()
        self._fill_bar()
        self._fill_dirs()
        self._fill_verdict()
        self.lbl_info.setText("已重新统计，体积变化见左侧")

    def _run_cmd(self, item: spacereport.Reclaim) -> None:
        """就地执行「禁用 / 清理」：先讲清代价，跑完立刻复测。"""
        verb = item.verb or "执行"
        box = QMessageBox(self)
        box.setWindowTitle(f"{verb}{item.name}")
        box.setIcon(QMessageBox.Warning)
        box.setTextFormat(Qt.PlainText)
        box.setText(f"将{verb}「{item.name}」，预计释放 {fmt_size(item.size)}。")
        note = item.warn or item.note
        box.setInformativeText(f"{note}\n\n这一步不能撤销，确定继续吗？")
        confirm = box.addButton(f"仍要{verb}", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not confirm:
            return

        code, output = spacereport.execute(item.cmd)
        if code != 0:
            detail = (output or "").strip()[-400:] or f"退出码 {code}"
            QMessageBox.warning(
                self,
                f"{verb}失败",
                f"{item.name}没有被释放。系统返回：\n\n{detail}",
            )
            return

        self.lbl_info.setText(f"已{verb} {item.name}，正在重测体积…")
        self._refresh_key = item.key
        name = item.name
        self.tasks.submit(
            "space_refresh",
            f"重测 {name}",
            lambda report, should_stop: spacereport.collect_one(item.key, should_stop),
        )

    def _run_action(self, item: spacereport.Reclaim) -> None:
        """可回收项的按钮。能就地做的就地做，做不了的跳页面或交给 Windows。"""
        if item.cmd:
            self._run_cmd(item)
            return
        page = item.page
        if page in ("slim", "recycle"):
            self._nav(page)
            return
        launched = False
        if page == "sysprops":
            launched = run_system("SystemPropertiesPerformance.exe")
        elif page == "restore":
            launched = run_system("SystemPropertiesProtection.exe")
        elif page == "index":
            launched = run_system("control.exe", "srchadmin.dll")
        elif page == "folder" and item.path:
            launched = open_folder(item.path)
        if not launched:
            QMessageBox.information(self, item.name, item.note)

    def _show_help(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("单分区机器的空间怎么腾")
        box.setTextFormat(Qt.PlainText)
        box.setText(
            "只有 C 盘时，能做的按收益排序：\n\n"
            "1. 关掉休眠：省下和内存等量的一块（这里是几 GB），代价是不能休眠\n"
            "2. 清更新缓存与回收站：干净、安全，随时会再长回来，隔几个月清一次\n"
            "3. 系统还原点只留最近一个：省几 GB，仍然能还原系统\n"
            "4. 组件存储清理（DISM）：能回收 1~5 GB，清完旧更新不可卸载\n"
            "5. 视频、游戏、网盘缓存搬到外接盘；软件本身别搬，会找不到路径\n\n"
            "不建议做的：手动删 Windows\\Installer（软件无法卸载）、"
            "删 System Volume Information（还原点全废）、直接删 pagefile.sys（系统不稳定）。"
        )
        box.exec()


ACTION_TEXT = {
    "clean": "去清理",
    "open": "打开",
    "disable": "禁用",
    "none": "仅统计",
}


# ------------------------------------------------------------------ 外壳


class SpacePage(QWidget):
    """磁盘空间：一个导航项，页内分段切换「空间分析 / 大文件」。"""

    segment_changed = Signal(str, str)

    def __init__(self, store, tasks, on_navigate=None, parent=None):
        super().__init__(parent)
        self._key = SEGMENTS[0][0]

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(7)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self.seg = Segmented(SEGMENTS)
        head.addWidget(self.seg)
        head.addStretch(1)
        root.addLayout(head)

        self.analysis = SpaceAnalysisView(store, tasks, on_navigate)
        self.files = BigFilePage(store, tasks)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.analysis)
        self.stack.addWidget(self.files)
        root.addWidget(self.stack, 1)

        self.seg.changed.connect(self._on_segment)
        # 页面是按需建的：能走到这里说明用户刚点开侧栏，直接开扫。
        # 有缓存（store 里有上次结果）时 ensure_scan 会自己跳过。
        QTimer.singleShot(0, self.analysis.ensure_scan)

    def _on_segment(self, key: str) -> None:
        self._key = key
        self.stack.setCurrentWidget(self.analysis if key == "analysis" else self.files)
        self.segment_changed.emit(key, SEGMENT_SUBS.get(key, ""))
        if key == "analysis":
            self.analysis.ensure_scan()

    def set_segment(self, key: str) -> None:
        if key not in dict(SEGMENTS):
            return
        self.seg.set_current(key, emit=False)
        self._on_segment(key)

    def current_segment(self) -> str:
        return self._key

    def subtitle(self) -> str:
        return SEGMENT_SUBS.get(self._key, "")

    def ensure_scan(self) -> None:
        self.analysis.ensure_scan()

    def start_scan(self) -> None:
        """F5：分析段重新扫，大文件段跑它自己的扫描。"""
        if self._key == "analysis":
            self.analysis.start_scan()
        else:
            self.files.start_scan()

    def shutdown(self) -> None:
        self.analysis.tasks.cancel_many("space", "space_sub", "space_refresh")
        self.files.shutdown()
