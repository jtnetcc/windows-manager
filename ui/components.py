"""深色界面的基础控件。"""

from __future__ import annotations

import math
import os
import subprocess
from collections import deque

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from . import icons, theme_dark as T
from core import applog


def font(size: int, weight: QFont.Weight = QFont.Normal) -> QFont:
    f = QFont("Segoe UI Variable Text")
    f.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"])
    f.setPixelSize(size)
    f.setWeight(weight)
    return f


def mono(size: int, weight: QFont.Weight = QFont.Normal) -> QFont:
    """等宽字体。数字一律用它：比例字每秒换值时字宽会变，整行在轻微呼吸。"""
    f = QFont("Consolas")
    f.setFamilies(["Consolas", "Cascadia Mono", "JetBrains Mono", "Courier New"])
    f.setPixelSize(size)
    f.setWeight(weight)
    f.setStyleHint(QFont.Monospace)
    return f


def label(text: str, size: int = 12, color: str = T.TEXT,
          weight: QFont.Weight = QFont.Normal) -> QLabel:
    lb = QLabel(text)
    lb.setFont(font(size, weight))
    lb.setStyleSheet(f"color: {color}; background: transparent; border: none;")
    return lb


def mono_label(text: str, size: int = 12, color: str = T.TEXT,
               weight: QFont.Weight = QFont.Normal) -> QLabel:
    lb = QLabel(text)
    lb.setFont(mono(size, weight))
    lb.setStyleSheet(f"color: {color}; background: transparent; border: none;")
    return lb


class ElidedLabel(QWidget):
    """单行文本：放不下时画省略号，完整内容进提示框。

    比 QLabel 好在两点：QLabel 遇到长文本是**直接裁掉**的，右边半个字都不剩，
    看着像坏了（用户的原话是「有的字都不显示了」）；而且 QLabel 的
    minimumSizeHint 按整段文本算，一个 40 字符的 NVMe 序列号就能把整列顶宽。

    这里自绘，sizeHint 不参与布局，宽度由父布局决定，超出的部分变成「…」。
    """

    def __init__(self, text: str = "", size: int = 12, color: str = T.TEXT,
                 weight: QFont.Weight = QFont.Normal, mode=Qt.ElideRight,
                 use_mono: bool = False, parent=None):
        super().__init__(parent)
        self._text = text
        self._color = color
        self._mode = mode
        self._font = mono(size, weight) if use_mono else font(size, weight)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setFixedHeight(20)
        if text:
            self.setToolTip(text)

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:
        self._text = text or ""
        self.setToolTip(self._text)
        self.update()

    def set_color(self, color: str) -> None:
        if self._color != color:
            self._color = color
            self.update()

    def contextMenuEvent(self, event) -> None:
        """右键复制。自绘之后拿不到文字选择，复制得另开一个口子 ——
        序列号、密钥这类东西用户是要拿去用的。"""
        menu = QMenu(self)
        action = menu.addAction("复制")
        action.setEnabled(bool(self._text))
        action.triggered.connect(lambda: copy_text(self._text))
        menu.exec(event.globalPos())

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setFont(self._font)
        painter.setPen(QColor(self._color))
        metrics = painter.fontMetrics()
        painter.drawText(
            self.rect(),
            Qt.AlignLeft | Qt.AlignVCenter,
            metrics.elidedText(self._text, self._mode, self.width()),
        )
        painter.end()


# 面板右上角切角尺寸。整套界面的形态语汇就靠这一个数：有切角 = 仪器面板，
# 纯圆角 = 网页卡片。
CHAMFER = 14.0


def panel_path(rect: QRectF, chamfer: float = CHAMFER) -> QPainterPath:
    """面板轮廓：右上角切一刀，其余保持直角。"""
    path = QPainterPath()
    path.moveTo(rect.left(), rect.top())
    path.lineTo(rect.right() - chamfer, rect.top())
    path.lineTo(rect.right(), rect.top() + chamfer)
    path.lineTo(rect.right(), rect.bottom())
    path.lineTo(rect.left(), rect.bottom())
    path.closeSubpath()
    return path


def paint_panel(painter: QPainter, rect: QRectF, fill: str,
                chamfer: float = CHAMFER) -> None:
    """填一块切角面板，并画出「顶边高光 + 其余发丝线」。

    四边等重的描边会让所有块面看起来一样重，所以顶边单独加粗到 1.6px：
    光从上面打下来，面板才有厚度。
    """
    path = panel_path(rect, chamfer)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(fill))
    painter.drawPath(path)

    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QColor(T.panel_edge()), 1))
    painter.drawPath(path)

    painter.setPen(QPen(QColor(T.panel_edge_top()), 1.6))
    painter.drawLine(
        QPointF(rect.left(), rect.top() + 0.8),
        QPointF(rect.right() - chamfer + 0.5, rect.top() + 0.8),
    )


def copy_text(text: str) -> None:
    """复制到剪贴板。

    四个页面的右键菜单都要用，原来各自写一份，连 `QApplication` 都在函数体里
    再 import 一次 —— 一模一样的三行抄了四遍。
    """
    QApplication.clipboard().setText(text)


def open_url(url: str) -> bool:
    """用系统默认浏览器打开链接。

    程序自己不联网 —— 这里只是把地址交给 Shell，请求由浏览器发出。返回 False
    让调用方能给出「打不开」的提示，而不是点了没反应。
    """
    if not url:
        return False
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    return QDesktopServices.openUrl(QUrl(url))


def reveal_file(path: str) -> None:
    """在资源管理器里定位文件。

    比「打开所在目录」好用的地方是直接选中目标。文件已经不在时（列表是上次
    扫描留下的）退回到父目录，否则 explorer 会弹一个空窗口。
    """
    if not path:
        return
    if os.path.exists(path):
        args = ["explorer", "/select,", path]
    else:
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            return
        args = ["explorer", parent]
    try:
        subprocess.Popen(args)
    except OSError:
        applog.silenced("components.reveal_file")


def open_folder(path: str) -> bool:
    """打开一个目录（不选中任何文件）。返回 False 让调用方能提示「打不开」。"""
    if not path or not os.path.isdir(path):
        return False
    try:
        os.startfile(path)
        return True
    except OSError:
        return False


def run_system(*args: str) -> bool:
    """拉起系统自带的设置界面。

    磁盘空间页有几个动作只能交给 Windows 自己做（保护点配额、索引重建、
    页面文件大小），硬做要改注册表或者调 WMI，风险远大于收益。
    """
    import subprocess

    try:
        subprocess.Popen(list(args), shell=False)
        return True
    except OSError:
        return False


def make_table(
    headers: list[str],
    *,
    row_height: int = 30,
    elide=Qt.ElideMiddle,
    wrap: bool = False,
    multiple: bool = False,
    striped: bool = True,
    menu: bool = False,
    styled: bool = True,
) -> QTableWidget:
    """统一表格外观：无网格、整行选中、不可编辑。

    七个页面原来各抄一份同样的十余行初始化，改一处样式得改七遍。
    这里只收敛「处处相同」的部分；列宽和拉伸模式差异太大，留给页面自己设。

    `menu=True` 只是打开右键策略，具体的 QMenu 仍由页面连
    `customContextMenuRequested` 构造 —— 每页的菜单项不一样。
    """

    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setShowGrid(False)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(row_height)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(
        QAbstractItemView.ExtendedSelection
        if multiple
        else QAbstractItemView.SingleSelection
    )
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setAlternatingRowColors(striped)
    table.setFocusPolicy(Qt.NoFocus)
    table.setWordWrap(wrap)
    table.setTextElideMode(elide)
    if menu:
        table.setContextMenuPolicy(Qt.CustomContextMenu)
    if styled:
        table.setStyleSheet(
            f"QTableWidget {{ font-size: 12px;"
            f" alternate-background-color: {T.ROW_ALT}; }}"
            f"QHeaderView::section {{ background: {T.PANEL}; border: none;"
            f" border-bottom: 1px solid {T.BORDER}; color: {T.TEXT_DIM};"
            f" font-size: 11px; padding: 0 4px 6px 4px; }}"
            f"QHeaderView::section:first {{ padding-left: 8px; }}"
        )
    header = table.horizontalHeader()
    header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    return table


class Hairline(QWidget):
    """贯穿整页的发丝线，左端压一个实心小方块。

    方块的作用是给这条线一个「起点」：一条没有端头的细线看起来像分隔符，
    带上起点标记之后就像仪表盘上的刻度基线。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(3)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setPen(QPen(QColor(T.panel_edge()), 1))
        painter.drawLine(QPointF(0, 1.5), QPointF(self.width(), 1.5))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(T.ACCENT))
        painter.drawRect(QRectF(0, 0, 7, 3))
        painter.end()


class Vitals(QWidget):
    """标题带上的全局体征：CPU / 内存 / 磁盘。

    以前只有监控页知道机器现在怎么样，切到清理页就得靠记忆。这块常驻在
    每一页的标题带右侧，任何时候都能扫一眼。

    宽度不足时从右往左少画几个，不会把按钮挤出去。
    """

    UNIT_W = 108

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(24)
        self._items = [
            ("CPU", T.ACCENT, deque(maxlen=32)),
            ("内存", T.PURPLE, deque(maxlen=32)),
            ("磁盘", T.CYAN, deque(maxlen=32)),
        ]
        self.setToolTip("CPU / 内存 / 磁盘当前占用")

    def push(self, values: list[float]) -> None:
        for (_name, _color, data), value in zip(self._items, values):
            data.append(max(0.0, min(100.0, float(value))))
        self.update()

    def _paint_unit(self, painter: QPainter, x: float, name: str,
                    color: str, data: deque) -> None:
        painter.setFont(font(11))
        painter.setPen(QColor(T.TEXT_DIM))
        painter.drawText(
            QRectF(x, 0, 30, self.height()), Qt.AlignLeft | Qt.AlignVCenter, name
        )

        value = data[-1] if data else 0.0
        painter.setFont(mono(12))
        painter.setPen(QColor(T.TEXT_SUB))
        painter.drawText(
            QRectF(x + 32, 0, 36, self.height()),
            Qt.AlignRight | Qt.AlignVCenter,
            f"{value:.0f}%",
        )

        # 迷你曲线：纵轴固定 0~100，形状才有可比性，不跟着窗口自己缩放。
        # 右侧留 14px 给相邻单元的分隔线，别让它切到曲线尾巴上。
        left, width, height = x + 72, 22.0, self.height() - 8
        top = (self.height() - height) / 2
        if len(data) < 2:
            return
        path = QPainterPath()
        step = width / max(1, len(data) - 1)
        for index, item in enumerate(data):
            px = left + index * step
            py = top + height * (1 - item / 100.0)
            if index == 0:
                path.moveTo(px, py)
            else:
                path.lineTo(px, py)
        painter.setPen(QPen(QColor(color), 1.2))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

        # 末端点：标出「当前」在曲线的哪个位置
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(color))
        painter.drawEllipse(
            QPointF(left + width, top + height * (1 - value / 100.0)), 1.8, 1.8
        )

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        fit = min(len(self._items), int(self.width() // self.UNIT_W))
        for index in range(fit):
            name, color, data = self._items[index]
            # 右对齐排布：紧贴右侧按钮，中间空出来的是标题的呼吸位
            x = self.width() - (fit - index) * self.UNIT_W
            if index:
                painter.setPen(QPen(QColor(T.panel_edge()), 1))
                painter.drawLine(
                    QPointF(x - 7, 5), QPointF(x - 7, self.height() - 5)
                )
            self._paint_unit(painter, x, name, color, data)
        painter.end()


class Card(QFrame):
    """面板容器。右上角切角，顶边一道高光。

    不用 QSS 画的原因有两个：QSS 做不出切角；而给容器设带通配符的样式表
    会级联到卡片里的按钮和输入框，把它们的边框一起冲掉。所以整块面板自绘，
    子控件保持自己的样式不动。
    """

    def __init__(self, parent=None, padding: int = 14, spacing: int = 10,
                 alt: bool = False, index: str | None = None):
        super().__init__(parent)
        self.setObjectName("Card")
        self._alt = alt
        self._pad = padding
        # 通道编号（01 / 02 …）。给了就在左上角画一行小字，内容整体下移。
        self._index = index
        self._layout = QVBoxLayout(self)
        top = padding + 15 if index else padding
        self._layout.setContentsMargins(padding, top, padding, padding)
        self._layout.setSpacing(spacing)

    def body(self) -> QVBoxLayout:
        return self._layout

    def add(self, widget: QWidget, stretch: int = 0):
        self._layout.addWidget(widget, stretch)
        return widget

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        paint_panel(painter, rect, T.PANEL_ALT if self._alt else T.PANEL)
        if self._index:
            painter.setFont(mono(10))
            painter.setPen(QColor(T.TEXT_DIM))
            painter.drawText(
                QRectF(self._pad, self._pad - 3, 40, 13),
                Qt.AlignLeft | Qt.AlignVCenter,
                self._index,
            )
            painter.setPen(QPen(QColor(T.BORDER_STRONG), 1))
            painter.drawLine(
                QPointF(self._pad + 18, self._pad),
                QPointF(self._pad + 18, self._pad + 8),
            )
        painter.end()


class Icon(QWidget):
    """按主题色绘制的线性图标。"""

    def __init__(self, name: str, size: int = 16, color: str | None = None, parent=None):
        super().__init__(parent)
        self._name = name
        self._color = color
        self.setFixedSize(size, size)

    def set_color(self, color: str) -> None:
        self._color = color
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        icons.draw(painter, self._name, QRectF(self.rect()), self._color or T.TEXT_SUB)
        painter.end()


class KpiCard(QFrame):
    """一个大数字 + 标题 + 副标题，标题前可带图标，底部可带一条占用比例细线。"""

    def __init__(self, title: str, unit: str = "", icon: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Kpi")
        self._unit = unit
        self._color = T.TEXT
        # 底部比例线：0~1，None 表示这个指标没有比例（比如网络速率）
        self._meter: float | None = None
        self._meter_color = T.ACCENT

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(1)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        if icon:
            head.addWidget(Icon(icon, 13, T.TEXT_DIM))
        self.lbl_title = label(title, 12, T.TEXT_DIM)
        head.addWidget(self.lbl_title)
        head.addStretch(1)
        layout.addLayout(head)

        # 数值用等宽字：每秒刷新时字宽不会跟着数字变，读数才不抖
        self.lbl_value = mono_label("--", 25, T.TEXT, QFont.DemiBold)
        self.lbl_sub = label("", 11, T.TEXT_DIM)
        layout.addWidget(self.lbl_value)
        layout.addWidget(self.lbl_sub)

    def set_value(
        self,
        value: str,
        sub: str = "",
        color: str | None = None,
        meter: float | None = None,
        meter_color: str | None = None,
    ) -> None:
        self.lbl_value.setText(f"{value}{self._unit}")
        self.lbl_sub.setText(sub)
        # 每秒都在调这个函数，而 setStyleSheet 会触发一次样式重算 + 重绘。
        # 负载颜色只在高/中/低三档之间跳，绝大多数帧是同一个值，没必要重设。
        if color and color != self._color:
            self._color = color
            self.lbl_value.setStyleSheet(
                f"color: {color}; background: transparent; border: none;"
            )
        # 底部比例线同理：只有跨过 1% 才重绘，避免每秒整张卡重画一次
        target = None if meter is None else max(0.0, min(1.0, float(meter)))
        tint = meter_color or T.ACCENT
        if target is None:
            if self._meter is not None:
                self._meter = None
                self.update()
        else:
            changed = self._meter is None or abs(target - self._meter) >= 0.01
            if changed or tint != self._meter_color:
                self._meter = target
                self._meter_color = tint
                self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        paint_panel(painter, rect, T.PANEL)
        if self._meter is None:
            painter.end()
            return
        # 比例线要贴着卡片下沿走，超出轮廓的部分裁掉
        painter.setClipPath(panel_path(rect))

        # 比例线改成分段电平：连续条形读起来像「进度条」，一格一格才像电量。
        # 同一套东西换个画法，观感就从表单控件挪到了仪表。
        bar = QRectF(14, self.height() - 8.0, self.width() - 28, 3.6)
        seg, gap = 5.0, 2.4
        count = max(1, int((bar.width() + gap) // (seg + gap)))
        lit = int(round(count * self._meter))

        painter.setPen(Qt.NoPen)
        for index in range(count):
            x = bar.x() + index * (seg + gap)
            if x + seg > bar.right() + 0.5:
                break
            painter.setBrush(
                QColor(self._meter_color if index < lit else T.BORDER_SOFT)
            )
            painter.drawRect(QRectF(x, bar.y(), seg, bar.height()))
        painter.end()


def _nice_ceiling(value: float) -> float:
    """把纵轴上限抬到一个「好看」的刻度（1/1.5/2/2.5/3/4/5/6/8 × 10^n）。

    自适应纵轴原来直接取峰值×1.18，于是刻度会写成 1523 KB/s 这种数字，
    轴上一眼看不出量级。
    """
    if value <= 0:
        return 1.0
    base = 10 ** math.floor(math.log10(value))
    for step in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if value <= step * base:
            return step * base
    return 10 * base


class TrendChart(QWidget):
    """多序列趋势图，纵轴按峰值自适应铺满，支持悬停读数。"""

    def __init__(self, series: list[tuple[str, str]], capacity: int = 180,
                 unit: str = "%", parent=None):
        super().__init__(parent)
        self._series = series
        self._unit = unit
        self._data = [deque(maxlen=capacity) for _ in series]
        self._hover_x: float | None = None
        self.setMinimumHeight(84)
        # 悬停读数需要实时鼠标位置，不按键也要收到 move 事件
        self.setMouseTracking(True)

    def push(self, values: list[float]) -> None:
        for i, value in enumerate(values[: len(self._data)]):
            self._data[i].append(float(value))
        self.update()

    def clear(self) -> None:
        for d in self._data:
            d.clear()
        self.update()

    def _paint_hover(self, painter, left_pad, plot_w, top_pad, plot_h) -> None:
        """在鼠标位置画一条竖线，并在角落显示该点的各序列数值。"""
        length = max((len(d) for d in self._data), default=0)
        if length < 2 or self._hover_x is None:
            return

        step = plot_w / max(1, length - 1)
        index = int(round((self._hover_x - left_pad) / step))
        index = max(0, min(length - 1, index))
        hx = left_pad + step * index

        painter.setPen(QPen(QColor(T.TEXT_DIM), 1, Qt.DashLine))
        painter.drawLine(int(hx), int(top_pad), int(hx), int(top_pad + plot_h))

        rows = []
        for i, (name, color) in enumerate(self._series):
            data = list(self._data[i])
            if index < len(data):
                value = data[index]
                text = (
                    f"{value:.0f}{self._unit}"
                    if self._unit == "%"
                    else f"{value:.1f}{self._unit}"
                )
                rows.append((name, text, color))
        if not rows:
            return

        painter.setFont(font(10))
        metrics = painter.fontMetrics()
        width = max(
            metrics.horizontalAdvance(f"{name} {text}") for name, text, _ in rows
        ) + 26
        height = len(rows) * 15 + 8

        # 靠近右边就翻到左侧，别画出画布外
        bx = hx + 10
        if bx + width > left_pad + plot_w:
            bx = hx - width - 10
        by = top_pad + 2

        box = QRectF(bx, by, width, height)
        painter.setPen(QPen(QColor(T.BORDER), 1))
        painter.setBrush(QColor(T.TOAST_BG))
        painter.drawRoundedRect(box, 4, 4)

        for row, (name, text, color) in enumerate(rows):
            y = by + 4 + row * 15
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(color))
            painter.drawEllipse(QRectF(bx + 7, y + 5, 5, 5))
            painter.setPen(QColor(T.TEXT))
            painter.drawText(
                QRectF(bx + 16, y, width - 20, 15),
                Qt.AlignLeft | Qt.AlignVCenter,
                f"{name} {text}",
            )

    def mouseMoveEvent(self, event) -> None:
        self._hover_x = float(event.position().x())
        self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_x = None
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect())

        all_values = [v for d in self._data for v in d]
        if len(all_values) < 2:
            painter.setPen(QColor(T.TEXT_DIM))
            painter.setFont(font(11))
            painter.drawText(rect, Qt.AlignCenter, "正在采集数据…")
            painter.end()
            return

        peak = max(all_values)
        if self._unit == "%":
            # 百分比按 10 的整数倍取上限：80/40/0 比 75/37/0 好读
            high = min(100.0, max(10.0, math.ceil(peak * 1.15 / 10) * 10))
        else:
            high = _nice_ceiling(peak * 1.15) if peak > 4 else 10.0
        low = 0.0
        span = high - low or 1.0

        # 多序列时左上角要放图例，给它单独留一条带子，别压在绘图区上
        top_pad = 18 if len(self._series) > 1 else 8
        # 右边留 10px：末端的环形标记有半径，贴着画布边会被裁掉半个
        left_pad, right_pad, bottom_pad = 38, 10, 6
        plot_w = rect.width() - left_pad - right_pad
        plot_h = rect.height() - top_pad - bottom_pad

        def y_for(value: float) -> float:
            return top_pad + plot_h * (1 - (value - low) / span)

        # 网格：改成点阵。横线会和曲线抢注意力，点阵退到背景里，
        # 但保留「有刻度」的感觉 —— 一格一格数得清，比虚线更像仪器。
        grid_pen = QPen(QColor(T.BORDER_STRONG), 1.6)
        grid_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(grid_pen)
        dots = []
        for frac in (0.0, 0.5, 1.0):
            y = top_pad + plot_h * frac
            x = left_pad
            while x <= left_pad + plot_w:
                dots.append(QPointF(x, y))
                x += 8.0
        if dots:
            painter.drawPoints(dots)

        # 纵轴刻度
        painter.setFont(mono(10))
        painter.setPen(QColor(T.TEXT_DIM))
        for frac, value in ((0.0, high), (0.5, high / 2), (1.0, 0.0)):
            y = top_pad + plot_h * frac
            text = f"{value:.0f}{self._unit}" if self._unit == "%" else f"{value:.0f}"
            painter.drawText(
                QRectF(0, y - 8, left_pad - 8, 16),
                Qt.AlignRight | Qt.AlignVCenter,
                text,
            )

        for index, (name, color) in enumerate(self._series):
            data = list(self._data[index])
            if len(data) < 2:
                continue
            step = plot_w / max(1, len(data) - 1)
            points = [(left_pad + step * i, y_for(v)) for i, v in enumerate(data)]

            area = QPainterPath()
            area.moveTo(points[0][0], top_pad + plot_h)
            for px, py in points:
                area.lineTo(px, py)
            area.lineTo(points[-1][0], top_pad + plot_h)
            area.closeSubpath()

            # 只在线下方留一段渐变光晕。整块铺满会显得很闷，
            # 数值平稳时整张图会变成一大片色块。
            # 渐变必须以「曲线最高点」为起点，从画布顶部起算的话，
            # 曲线靠上的时候整段填充都会落在透明区间里，等于没画。
            peak_y = min(py for _, py in points)
            # 多序列时把底色压得很淡：两条曲线各铺一大片会把图变成拼色块，
            # 重点应该落在线上。单序列（CPU 页/内存页）保留原来的深度。
            single = len(self._series) == 1
            grad = QLinearGradient(
                0, peak_y, 0, peak_y + plot_h * (0.55 if single else 0.34)
            )
            top = QColor(color)
            top.setAlpha(56 if single else 22)
            clear = QColor(color)
            clear.setAlpha(0)
            grad.setColorAt(0.0, top)
            grad.setColorAt(1.0, clear)
            painter.fillPath(area, grad)

            line = QPainterPath()
            line.moveTo(*points[0])
            for px, py in points[1:]:
                line.lineTo(px, py)

            # 光晕：同一根线先画两遍粗的、低透明度的，再压一遍实线。
            # 比真的做模糊便宜得多，一秒一帧也不会掉。
            painter.setBrush(Qt.NoBrush)
            for width_px, alpha in ((6.5, 22), (3.2, 44)):
                halo = QColor(color)
                halo.setAlpha(alpha)
                glow = QPen(halo, width_px)
                glow.setCapStyle(Qt.RoundCap)
                glow.setJoinStyle(Qt.RoundJoin)
                painter.setPen(glow)
                painter.drawPath(line)

            pen = QPen(QColor(color), 1.6)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            painter.drawPath(line)

            # 当前点：空心环 + 实心点。环让人一眼看到「读数停在哪」，
            # 单纯一个点太容易和曲线拐点混在一起。
            ex, ey = points[-1]
            ring = QColor(color)
            ring.setAlpha(90)
            painter.setPen(QPen(ring, 1.2))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QPointF(ex, ey), 4.6, 4.6)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(color))
            painter.drawEllipse(QPointF(ex, ey), 2.0, 2.0)

        # 悬停读数：竖线 + 数值气泡
        if self._hover_x is not None and left_pad <= self._hover_x <= left_pad + plot_w:
            self._paint_hover(
                painter, left_pad, plot_w, top_pad, plot_h
            )

        # 图例
        if len(self._series) > 1:
            painter.setFont(font(10))
            x = left_pad
            for name, color in self._series:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(color))
                painter.drawEllipse(QRectF(x, 2, 5, 5))
                painter.setPen(QColor(T.TEXT_DIM))
                painter.drawText(QRectF(x + 9, -4, 70, 16),
                                 Qt.AlignLeft | Qt.AlignVCenter, name)
                x += 78
        painter.end()


class CoreTiles(QWidget):
    """每核占用：一个核心一块方砖，底色深浅表示负载。

    原来是竖柱。四核机器上四根柱子被拉到跟卡片一样高，一大片全是空轨道，
    反而看不出谁在忙；方砖在小核数时不空旷，核多时按 8 列折行，32 线程也
    能一屏放下。读数（精确百分比）交给 CPU 页的「CPU 占用 Top」，
    砖块只承担「一眼看出哪几核在忙」。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._values: list[float] = []
        self.setMinimumHeight(96)

    def set_values(self, values: list[float]) -> None:
        self._values = list(values)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        values = self._values

        if not values:
            painter.end()
            return

        count = len(values)
        gap = 6
        # 卡片很高，固定一排会让方块缩在中间、上下全空。这里按「格子尽量
        # 接近正方形」挑列数：4 核排 2×2、8 核 3×3、16 核 4×4，砖块自然
        # 把卡片填满。
        cols, tile_w, tile_h = 1, 0.0, 0.0
        best = None
        for candidate in range(1, min(count, 8) + 1):
            r = math.ceil(count / candidate)
            w = (self.width() - gap * (candidate - 1)) / candidate
            h = (self.height() - gap * (r - 1)) / r
            ratio = max(w / h, h / w) if h > 0 else 99
            # 越方越好，但别留下空格子：4 核排成 3 列会在右下角空一块，
            # 看着像少画了一个核，宁可稍微扁一点也排成 2×2
            empty = candidate * r - count
            score = (round(ratio + empty * 0.45, 2), -w * h)
            if best is None or score < best:
                best, cols, tile_w, tile_h = score, candidate, w, h
        # 太瘦的柱子又会变成原来那种「高而空」，压一下宽高比
        if tile_h > tile_w * 1.8:
            tile_h = tile_w * 1.8
        rows = math.ceil(count / cols)

        total_w = tile_w * cols + gap * (cols - 1)
        total_h = tile_h * rows + gap * (rows - 1)
        x0 = (self.width() - total_w) / 2
        y0 = (self.height() - total_h) / 2
        radius = max(4.0, min(9.0, min(tile_w, tile_h) * 0.14))

        for index, value in enumerate(values):
            row, col = divmod(index, cols)
            rect = QRectF(
                x0 + col * (tile_w + gap), y0 + row * (tile_h + gap), tile_w, tile_h
            )
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(T.PANEL_ALT if T.is_dark() else T.PANEL_SUNKEN))
            painter.drawRoundedRect(rect, radius, radius)

            # 底色承担「一眼看高低」，透明度跟着负载走；画满会盖住数字
            load = max(0.0, min(100.0, value))
            heat = QColor(T.load_color(value))
            # 开方而不是线性：日常负载都在 10~30%，线性映射下四块颜色几乎一样，
            # 看不出谁更忙。开方把低负载段的差异拉开，上限仍压在 145 保文字对比度
            heat.setAlpha(int(28 + 118 * math.sqrt(load / 100)))
            painter.setBrush(heat)
            painter.drawRoundedRect(rect, radius, radius)

            # 砖块太小时写不下字，光靠底色区分
            short = min(tile_w, tile_h)
            if short < 24:
                continue
            if short >= 54:
                # 砖块够大时写全信息：左上「核 N」、中间百分比，等于一张小卡片
                painter.setPen(QColor(T.TEXT_DIM))
                painter.setFont(font(9))
                painter.drawText(
                    rect.adjusted(7, 5, -7, 0),
                    Qt.AlignLeft | Qt.AlignTop,
                    f"核 {index}",
                )
                painter.setPen(QColor(T.TEXT))
                painter.setFont(font(18, QFont.DemiBold))
                painter.drawText(
                    rect.adjusted(0, 0, 0, -radius * 0.5),
                    Qt.AlignCenter,
                    f"{load:.0f}%",
                )
            else:
                painter.setPen(QColor(T.TEXT))
                painter.setFont(font(11 if short >= 36 else 10, QFont.DemiBold))
                painter.drawText(
                    rect.adjusted(0, 0, 0, -radius * 0.6), Qt.AlignCenter, str(index)
                )
        painter.end()


class SegmentBar(QWidget):
    """分段构成条。"""

    def __init__(self, parent=None, height: int = 10):
        super().__init__(parent)
        self._segments: list[tuple[float, str]] = []
        self.setFixedHeight(height)

    def set_segments(self, segments: list[tuple[float, str]]) -> None:
        self._segments = segments
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        painter.setClipPath(clip)
        painter.fillRect(rect, QColor(T.BORDER_SOFT))

        x = 0.0
        total = sum(r for r, _ in self._segments) or 1.0
        for index, (ratio, color) in enumerate(self._segments):
            width = rect.width() * (ratio / total)
            gap = 0.0 if index == 0 else 1.5
            painter.fillRect(
                QRectF(x + gap, 0, max(0.0, width - gap), rect.height()),
                QColor(color),
            )
            x += width
        painter.end()


class Dot(QWidget):
    """图例：色点 + 名称 + 数值。"""

    def __init__(self, color: str, title: str, parent=None):
        super().__init__(parent)
        self._color = color
        self._title = title
        self._value = "--"
        self.setMinimumHeight(18)

    def set_value(self, text: str) -> None:
        self._value = text
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self._color))
        painter.drawEllipse(QRectF(1, self.height() / 2 - 3, 6, 6))

        painter.setFont(font(11))
        painter.setPen(QColor(T.TEXT_SUB))
        text_rect = QRectF(12, 0, self.width() - 12, self.height())
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, self._title)

        title_w = painter.fontMetrics().horizontalAdvance(self._title)
        painter.setPen(QColor(T.TEXT))
        painter.drawText(
            text_rect.adjusted(title_w + 7, 0, 0, 0),
            Qt.AlignLeft | Qt.AlignVCenter,
            self._value,
        )
        painter.end()


class Segmented(QWidget):
    """页内分段控件（Win11 Segmented）。

    用在「监控」页切换 总览 / CPU / 内存 / 磁盘 / 网络 / 进程。
    自绘而不是拼一排 QPushButton：轨道、选中胶囊、悬停底色要共用圆角，
    用样式表拼多个控件很难保证边缘对齐；宽度也按文字量算，中文不会截断。
    """

    changed = Signal(str)

    PAD = 3
    HEIGHT = 28

    def __init__(self, items: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self._items = list(items)
        self._keys = [key for key, _text in self._items]
        self._key = self._keys[0] if self._keys else ""
        self._hover = -1
        self._widths = [self._measure(text) for _key, text in self._items]
        self.setFixedHeight(self.HEIGHT)
        self.setFixedWidth(sum(self._widths) + self.PAD * 2)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.NoFocus)

    @staticmethod
    def _measure(text: str) -> int:
        return QFontMetrics(font(13)).horizontalAdvance(text) + 34

    def current(self) -> str:
        return self._key

    def set_current(self, key: str, emit: bool = True) -> None:
        if key not in self._keys or key == self._key:
            return
        self._key = key
        self._hover = -1
        self.update()
        if emit:
            self.changed.emit(key)

    def _index_at(self, x: float) -> int:
        left = self.PAD
        for index, width in enumerate(self._widths):
            if left <= x < left + width:
                return index
            left += width
        return -1

    def _item_rect(self, index: int) -> QRectF:
        left = self.PAD + sum(self._widths[:index])
        return QRectF(left, self.PAD, self._widths[index], self.HEIGHT - self.PAD * 2)

    def mouseMoveEvent(self, event) -> None:
        index = self._index_at(event.position().x())
        if index != self._hover:
            self._hover = index
            self.update()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            index = self._index_at(event.position().x())
            if index >= 0:
                self.set_current(self._keys[index])
            event.accept()
            return
        super().mousePressEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hover != -1:
            self._hover = -1
            self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        active = self._keys.index(self._key) if self._key in self._keys else -1
        metrics = QFontMetrics(font(13, QFont.DemiBold))

        for index, (_key, text) in enumerate(self._items):
            rect = self._item_rect(index)
            is_active = index == active
            if is_active:
                painter.setFont(font(13, QFont.DemiBold))
                painter.setPen(QColor(T.ACCENT))
            else:
                # 悬停只提亮文字，不铺底色：底色留给选中项，少一层噪音
                painter.setFont(font(13))
                painter.setPen(QColor(T.TEXT_SUB if index == self._hover else T.TEXT_DIM))
            painter.drawText(rect, Qt.AlignCenter, text)

            if is_active:
                # 下划强调线，宽度贴着文字，不做成整格
                width = metrics.horizontalAdvance(text)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(T.ACCENT))
                painter.drawRect(
                    QRectF(rect.center().x() - width / 2, self.HEIGHT - 2.0, width, 2.0)
                )
            if index:
                x = rect.left() - self.PAD / 2
                painter.setPen(QPen(QColor(T.BORDER), 1))
                painter.drawLine(
                    QPointF(x, rect.top() + 6), QPointF(x, rect.bottom() - 6)
                )
        painter.end()


class HoverMixin:
    """鼠标悬停状态。子类在 paintEvent 里读 `self._hover` 决定底色。

    可点行和自绘小控件原来各写一遍 enterEvent / leaveEvent，一共六份。
    复制时最容易漏的是 `update()`，表现就是「移上去不变色、移开才变色」，
    所以收成一份。子类如果还要处理别的状态（比如按下态），覆盖时记得
    调一次 super()。
    """

    _hover = False

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()


class NavButton(HoverMixin, QWidget):
    """侧栏导航项（Win11 NavigationView 样式）。

    选中态 = 卡片底色 + 左侧 3×16 强调色竖条，和系统设置一致。

    点击手感相关几点：
    - 整行 32px 都可点，不留布局间距，避免点在缝隙里没反应
    - 按下立刻变深、松开才切换页面，任何一次点击都有视觉反馈
    - 按下后拖出范围再松开则取消，跟系统控件一致
    """

    ROW_HEIGHT = 32

    def __init__(self, key: str, text: str, on_click, icon: str = "", parent=None):
        super().__init__(parent)
        self.key = key
        self._text = text
        self._icon = icon
        self._active = False
        self._pressed = False
        self._compact = False
        self._on_click = on_click
        self.setFixedHeight(self.ROW_HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.NoFocus)

    def set_active(self, active: bool) -> None:
        if self._active != active:
            self._active = active
            self.update()

    def set_compact(self, compact: bool) -> None:
        """只留图标。文字挪进提示框，不然收起之后每一行都成了谜语。"""
        if self._compact == compact:
            return
        self._compact = compact
        self.setToolTip(self._text if compact else "")
        self.update()

    def leaveEvent(self, event) -> None:
        # 按下后拖出范围再松开算取消，所以离开时除了清悬停还要清按下态
        if self._pressed:
            self._pressed = False
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._pressed:
            self._pressed = False
            self.update()
            if self.rect().contains(event.position().toPoint()):
                self._on_click(self.key)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        # 左右各留 6px，让选中底色和侧栏边缘有呼吸感
        rect = QRectF(self.rect()).adjusted(6, 1, -6, -1)

        if self._active:
            # 选中底色统一走强调色的淡版：深色下是暗青，浅色下是极淡青。
            # 原来浅色用 PANEL_SUNKEN（灰），和侧栏底色几乎同色，选中等于没选。
            active_bg = T.ACCENT_SOFT
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(active_bg))
            painter.drawRoundedRect(rect, 5, 5)
        elif self._pressed:
            painter.setPen(Qt.NoPen)
            # 轨道要够淡：太明显的话四个空槽会抢走注意力
            painter.setBrush(QColor(T.PANEL_ALT if T.is_dark() else T.PANEL_SUNKEN))
            painter.drawRoundedRect(rect, 5, 5)
        elif self._hover:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(T.HOVER))
            painter.drawRoundedRect(rect, 5, 5)

        # 选中指示条：3×16 圆角，贴在最左侧
        if self._active:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(T.ACCENT))
            painter.drawRoundedRect(
                QRectF(rect.left() + 1, rect.center().y() - 8, 3, 16), 1.5, 1.5
            )

        # 图标与文字：图标 16px，间距 11px，和设计稿一致
        color = T.ACCENT if self._active else T.TEXT_SUB
        icon_color = T.ACCENT if self._active else T.TEXT_SUB
        text_left = 16.0

        if self._compact:
            # 收起态：图标居中，文字交给提示框
            if self._icon:
                box = QRectF(0, 0, 16, 16)
                box.moveCenter(rect.center())
                if not icons.draw(painter, self._icon, box, icon_color):
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor(icon_color))
                    painter.drawEllipse(box.adjusted(4, 4, -4, -4))
            painter.end()
            return

        if self._icon:
            box = QRectF(rect.left() + 12, rect.center().y() - 8, 16, 16)
            if not icons.draw(painter, self._icon, box, icon_color):
                # 图标缺失时退化成一个小圆点，至少不留空洞
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(icon_color))
                painter.drawEllipse(box.adjusted(4, 4, -4, -4))
            text_left = 39.0

        painter.setFont(font(13, QFont.DemiBold if self._active else QFont.Normal))
        painter.setPen(QColor(color))
        painter.drawText(
            rect.adjusted(text_left, 0, -8, 0),
            Qt.AlignLeft | Qt.AlignVCenter,
            self._text,
        )
        painter.end()


class ComboBox(QComboBox):
    """下拉框：样式表里画不出箭头，自己补一个。

    只给 QComboBox 设背景/边框后，Qt 会走样式表绘制路径，原生的下拉箭头
    就不见了，用户根本看不出这是个下拉框。这里在 paintEvent 里补一个雪佛龙。
    """

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor(T.TEXT_SUB), 1.4)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)

        cx = self.width() - 14.0
        cy = self.height() / 2.0
        painter.drawLine(QPointF(cx - 4, cy - 2), QPointF(cx, cy + 2))
        painter.drawLine(QPointF(cx, cy + 2), QPointF(cx + 4, cy - 2))
        painter.end()


class CheckBox(HoverMixin, QAbstractButton):
    """自绘复选框。

    样式表里没法给 QCheckBox 的指示器画对勾（只能填一块纯色），
    看起来像一个实心方块，所以自己画。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(16, 16)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setChecked(True)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)

        if self.isChecked():
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(T.ACCENT))
            painter.drawRoundedRect(rect, 4, 4)

            path = QPainterPath()
            left = rect.left() + rect.width() * 0.24
            mid_x = rect.left() + rect.width() * 0.44
            mid_y = rect.top() + rect.height() * 0.66
            right = rect.left() + rect.width() * 0.76
            top_y = rect.top() + rect.height() * 0.34
            path.moveTo(left, rect.center().y())
            path.lineTo(mid_x, mid_y)
            path.lineTo(right, top_y)
            pen = QPen(QColor(T.ON_ACCENT), 1.8)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
        else:
            painter.setPen(
                QPen(
                    QColor(T.ACCENT if self._hover else T.BORDER_STRONG),
                    1.3,
                )
            )
            painter.setBrush(QColor(T.PANEL_ALT))
            painter.drawRoundedRect(rect, 4, 4)
        painter.end()


class ScoreRing(QWidget):
    """体检分数环：270° 圆弧 + 中间大数字。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._score = 0
        self._level = "—"
        self._color = T.TEXT_DIM
        self._sub = ""
        self.setMinimumSize(150, 150)
        self.setMaximumSize(190, 190)

    def set_score(self, score: int, level: str, color: str, sub: str = "") -> None:
        self._score = max(0, min(100, int(score)))
        self._level = level
        self._color = color
        self._sub = sub
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        side = min(self.width(), self.height()) - 18
        rect = QRectF(
            (self.width() - side) / 2,
            (self.height() - side) / 2,
            side,
            side,
        )
        pen = QPen(QColor(T.TRACK), 9)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        # 从 225° 开始逆时针画 270°，留出下方缺口
        painter.drawArc(rect, 225 * 16, -270 * 16)

        if self._score > 0:
            pen = QPen(QColor(self._color), 9)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawArc(rect, 225 * 16, int(-270 * 16 * self._score / 100))

        painter.setPen(QColor(self._color))
        painter.setFont(font(int(side * 0.30), QFont.DemiBold))
        painter.drawText(
            QRectF(rect.left(), rect.center().y() - side * 0.26, side, side * 0.36),
            Qt.AlignCenter,
            str(self._score),
        )

        painter.setPen(QColor(T.TEXT_SUB))
        painter.setFont(font(12, QFont.Medium))
        painter.drawText(
            QRectF(rect.left(), rect.center().y() + side * 0.08, side, 20),
            Qt.AlignCenter,
            self._level,
        )

        if self._sub:
            painter.setPen(QColor(T.TEXT_DIM))
            painter.setFont(font(10))
            painter.drawText(
                QRectF(rect.left(), rect.center().y() + side * 0.08 + 20, side, 18),
                Qt.AlignCenter,
                self._sub,
            )
        painter.end()


class MiniBar(QWidget):
    """细进度条，用于因子明细。"""

    def __init__(self, height: int = 6, parent=None):
        super().__init__(parent)
        self._ratio = 0.0
        self._color = T.ACCENT
        self._muted = False
        self.setFixedHeight(height)

    def set_value(self, ratio: float, color: str, muted: bool = False) -> None:
        self._ratio = max(0.0, min(1.0, ratio))
        self._color = color
        self._muted = muted
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect())
        radius = rect.height() / 2
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(T.BORDER_SOFT))
        painter.drawRoundedRect(rect, radius, radius)
        if self._ratio > 0:
            color = QColor(T.TEXT_DIM if self._muted else self._color)
            if self._muted:
                color.setAlpha(90)
            painter.setBrush(color)
            painter.drawRoundedRect(
                QRectF(rect.left(), rect.top(), rect.width() * self._ratio, rect.height()),
                radius,
                radius,
            )
        painter.end()


class RankList(QWidget):
    """紧凑排行列表：名称 + 角标 + 右侧数值。

    行控件只建一次、反复改文字，避免每次刷新都 deleteLater 一堆 QWidget
    （概览页原来每 3 秒重建 6 行，布局抖动很明显）。
    """

    def __init__(self, rows: int = 6, elide_width: int = 150, parent=None):
        super().__init__(parent)
        self._elide = elide_width
        self._rows: list[tuple[QWidget, QLabel, QLabel, QLabel]] = []
        # 上次写进样式表的颜色，用来跳过重复的 setStyleSheet
        self._colors: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        for _ in range(rows):
            line = QWidget()
            line.setFixedHeight(21)
            row = QHBoxLayout(line)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(5)

            name = label("", 11, T.TEXT)
            badge = label("", 10, T.TEXT_DIM)
            value = label("", 11, T.TEXT_SUB)
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

            row.addWidget(name, 0)
            row.addWidget(badge, 0)
            row.addStretch(1)
            row.addWidget(value, 0)
            layout.addWidget(line)
            self._rows.append((line, name, badge, value))
            self._colors.append("")

        layout.addStretch(1)

    def set_rows(self, items: list[tuple]) -> None:
        """items: (名称, 角标, 数值) 或 (名称, 角标, 数值, 数值颜色)"""
        metrics = self._rows[0][1].fontMetrics() if self._rows else None
        for index, widgets in enumerate(self._rows):
            line, name, badge, value = widgets
            if index >= len(items):
                line.hide()
                continue

            item = items[index]
            text = str(item[0])
            extra = item[1] if len(item) > 1 else ""
            amount = item[2] if len(item) > 2 else ""
            color = item[3] if len(item) > 3 else T.TEXT_SUB

            line.show()
            shown = metrics.elidedText(text, Qt.ElideRight, self._elide) if metrics else text
            name.setText(shown)
            name.setToolTip(text if shown != text else "")
            badge.setText(extra)
            badge.setVisible(bool(extra))
            value.setText(amount)
            # 颜色不变就别重设样式表：这个列表每 1~3 秒刷一次，
            # 每次 setStyleSheet 都会让 Qt 重新算一遍样式并重绘整行
            if color != self._colors[index]:
                self._colors[index] = color
                value.setStyleSheet(
                    f"color: {color}; background: transparent; border: none;"
                )

    def clear(self) -> None:
        self.set_rows([])


class AccentDot(HoverMixin, QWidget):
    """强调色选择点：一个圆点，选中时外面套一圈描边。

    `system=True` 的那个点代表「跟随 Windows 强调色」，它显示的颜色就是
    系统当前色，所以和其他点可能撞色 —— 常驻一圈虚线环来区分。
    点击时发出去的是空字符串，上层据此存成「不指定」。
    """

    clicked = Signal(str)

    def __init__(self, color: str, selected: bool = False, parent=None,
                 value: str | None = None, system: bool = False):
        super().__init__(parent)
        self._color = color
        self._value = color if value is None else value
        self._system = system
        self._selected = selected
        self.setFixedSize(24, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setFocusPolicy(Qt.NoFocus)
        if system:
            self.setToolTip("跟随 Windows 系统强调色")

    def value(self) -> str:
        return self._value

    def is_system(self) -> bool:
        return self._system

    def set_color(self, color: str) -> None:
        if self._color != color:
            self._color = color
            self.update()

    def set_selected(self, selected: bool) -> None:
        if self._selected != selected:
            self._selected = selected
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit(self._value)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        center = QRectF(self.rect()).center()
        radius = 7.0 if self._hover or self._selected else 6.5

        if self._selected:
            painter.setPen(QPen(QColor(T.TEXT_SUB), 1.2))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(center, 11, 11)
        elif self._system:
            pen = QPen(QColor(T.TEXT_DIM), 1.0, Qt.DashLine)
            pen.setDashPattern([2, 2])
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(center, 11, 11)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self._color))
        painter.drawEllipse(center, radius, radius)

        if self._system:
            # 中心留个白点：系统色往往和某个预设色相同，没有这个点
            # 两个圆看起来一模一样，用户不知道哪个是「自动」
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawEllipse(center, 2.2, 2.2)
        painter.end()


class ToggleSwitch(QAbstractButton):
    """深色开关，替代带文字的复选框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(40, 20)
        self.setCursor(Qt.PointingHandCursor)
        self._offset = 0.0
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def get_offset(self) -> float:
        return self._offset

    def set_offset(self, value: float) -> None:
        self._offset = float(value)
        self.update()

    offset = Property(float, get_offset, set_offset)

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def set_checked_silently(self, checked: bool) -> None:
        self.blockSignals(True)
        self.setChecked(checked)
        self.blockSignals(False)
        self._anim.stop()
        self.set_offset(1.0 if checked else 0.0)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        track = QRectF(self.rect()).adjusted(0, 3, 0, -3)

        off = QColor(T.TRACK)
        on = QColor(T.ACCENT)
        color = QColor(
            int(off.red() + (on.red() - off.red()) * self._offset),
            int(off.green() + (on.green() - off.green()) * self._offset),
            int(off.blue() + (on.blue() - off.blue()) * self._offset),
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(track, track.height() / 2, track.height() / 2)

        knob = track.height() - 4
        x = track.left() + 2 + (track.width() - knob - 4) * self._offset
        painter.setBrush(QColor(T.KNOB))
        painter.drawEllipse(QRectF(x, track.top() + 2, knob, knob))
        painter.end()
