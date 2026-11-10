"""深色界面的基础控件。"""

from __future__ import annotations

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
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from . import icons, theme_dark as T


def font(size: int, weight: QFont.Weight = QFont.Normal) -> QFont:
    f = QFont("Segoe UI Variable Text")
    f.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"])
    f.setPixelSize(size)
    f.setWeight(weight)
    return f


def label(text: str, size: int = 12, color: str = T.TEXT,
          weight: QFont.Weight = QFont.Normal) -> QLabel:
    lb = QLabel(text)
    lb.setFont(font(size, weight))
    lb.setStyleSheet(f"color: {color}; background: transparent; border: none;")
    return lb


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
        pass


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


class Card(QFrame):
    """面板容器。"""

    def __init__(self, parent=None, padding: int = 14, spacing: int = 10,
                 alt: bool = False):
        super().__init__(parent)
        self.setObjectName("Card")
        bg = T.PANEL_ALT if alt else T.PANEL
        self.setStyleSheet(
            f"#Card {{ background: {bg}; border: 1px solid {T.BORDER};"
            f" border-radius: 8px; }}"
        )
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(padding, padding, padding, padding)
        self._layout.setSpacing(spacing)

    def body(self) -> QVBoxLayout:
        return self._layout

    def add(self, widget: QWidget, stretch: int = 0):
        self._layout.addWidget(widget, stretch)
        return widget


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
    """一个大数字 + 标题 + 副标题，标题前可带图标。"""

    def __init__(self, title: str, unit: str = "", icon: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Kpi")
        self.setStyleSheet(
            f"#Kpi {{ background: {T.PANEL}; border: 1px solid {T.BORDER};"
            f" border-radius: 8px; }}"
        )
        self._unit = unit
        self._color = T.TEXT

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

        self.lbl_value = label("--", 26, T.TEXT, QFont.DemiBold)
        self.lbl_sub = label("", 11, T.TEXT_DIM)
        layout.addWidget(self.lbl_value)
        layout.addWidget(self.lbl_sub)

    def set_value(self, value: str, sub: str = "", color: str | None = None) -> None:
        self.lbl_value.setText(f"{value}{self._unit}")
        self.lbl_sub.setText(sub)
        # 每秒都在调这个函数，而 setStyleSheet 会触发一次样式重算 + 重绘。
        # 负载颜色只在高/中/低三档之间跳，绝大多数帧是同一个值，没必要重设。
        if color and color != self._color:
            self._color = color
            self.lbl_value.setStyleSheet(
                f"color: {color}; background: transparent; border: none;"
            )


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
        high = peak * 1.18 if peak > 4 else 10.0
        if self._unit == "%":
            high = min(100.0, max(high, 10.0))
        low = 0.0
        span = high - low or 1.0

        left_pad, right_pad, top_pad, bottom_pad = 38, 6, 8, 6
        plot_w = rect.width() - left_pad - right_pad
        plot_h = rect.height() - top_pad - bottom_pad

        def y_for(value: float) -> float:
            return top_pad + plot_h * (1 - (value - low) / span)

        # 网格
        painter.setPen(QPen(QColor(T.BORDER_SOFT), 1, Qt.DashLine))
        for frac in (0.0, 0.5, 1.0):
            y = top_pad + plot_h * frac
            painter.drawLine(int(left_pad), int(y), int(left_pad + plot_w), int(y))

        # 纵轴刻度
        painter.setFont(font(10))
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
            grad = QLinearGradient(0, peak_y, 0, peak_y + plot_h * 0.55)
            top = QColor(color)
            top.setAlpha(56)
            clear = QColor(color)
            clear.setAlpha(0)
            grad.setColorAt(0.0, top)
            grad.setColorAt(1.0, clear)
            painter.fillPath(area, grad)

            line = QPainterPath()
            line.moveTo(*points[0])
            for px, py in points[1:]:
                line.lineTo(px, py)
            pen = QPen(QColor(color), 1.6)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(line)

            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(color))
            painter.drawEllipse(
                QRectF(points[-1][0] - 2.4, points[-1][1] - 2.4, 4.8, 4.8)
            )

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


class CoreBars(QWidget):
    """每核占用柱状图：底部轨道 + 彩色柱。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._values: list[float] = []
        self.setMinimumHeight(58)

    def set_values(self, values: list[float]) -> None:
        self._values = list(values)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect())
        values = self._values

        if not values:
            painter.end()
            return

        count = len(values)
        gap = 6 if count <= 8 else 3
        bar_w = (rect.width() - gap * (count - 1)) / count
        bar_w = max(3.0, min(42.0, bar_w))
        total_w = bar_w * count + gap * (count - 1)
        x0 = (rect.width() - total_w) / 2

        # 轨道铺满可用高度；卡片变矮时自然收缩，不会出现柱子悬在半空
        bottom = rect.height() - 4
        track_h = max(24.0, bottom - 6)
        top = bottom - track_h

        for i, value in enumerate(values):
            x = x0 + i * (bar_w + gap)
            painter.setPen(Qt.NoPen)
            # 轨道要够淡：太明显的话四个空槽会抢走注意力
            painter.setBrush(QColor(T.PANEL_ALT if T.is_dark() else T.PANEL_SUNKEN))
            painter.drawRoundedRect(QRectF(x, top, bar_w, track_h), 2.5, 2.5)

            height = track_h * max(0.0, min(100.0, value)) / 100.0
            if height > 1:
                color = QColor(T.load_color(value))
                painter.setBrush(color)
                painter.drawRoundedRect(
                    QRectF(x, bottom - height, bar_w, height), 2.5, 2.5
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

        # 轨道：比页面底色深一档，选中胶囊才浮得起来
        painter.setPen(QPen(QColor(T.BORDER), 1))
        painter.setBrush(QColor(T.PANEL_SUNKEN))
        painter.drawRoundedRect(
            QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8
        )

        if self._hover >= 0 and self._keys[self._hover] != self._key:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(T.HOVER))
            painter.drawRoundedRect(self._item_rect(self._hover), 6, 6)

        active = self._keys.index(self._key) if self._key in self._keys else -1
        if active >= 0:
            painter.setPen(QPen(QColor(T.BORDER), 1))
            painter.setBrush(QColor(T.PANEL))
            painter.drawRoundedRect(self._item_rect(active), 6, 6)

        for index, (_key, text) in enumerate(self._items):
            is_active = index == active
            painter.setFont(font(13, QFont.DemiBold if is_active else QFont.Normal))
            # 选中项用强调色：浅色主题下轨道和胶囊都是灰白，只靠字重分不出来
            painter.setPen(QColor(T.ACCENT if is_active else T.TEXT_SUB))
            painter.drawText(
                self._item_rect(index), Qt.AlignCenter, text
            )
        painter.end()


class NavButton(QWidget):
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
        self._hover = False
        self._pressed = False
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

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = False
        if self._pressed:
            self._pressed = False
        self.update()

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
            # 浅色主题下侧栏是 #F9F9F9、卡片是 #FFFFFF，两者几乎同色，
            # 选中态会看不出来，所以浅色改用更深一档的面板底色
            active_bg = T.PANEL if T.is_dark() else T.PANEL_SUNKEN
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
        color = T.TEXT if self._active else T.TEXT_SUB
        icon_color = T.ACCENT if self._active else T.TEXT_SUB
        text_left = 16.0
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


class CheckBox(QAbstractButton):
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
        self._hover = False
        self.setChecked(True)

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()

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


class AccentDot(QWidget):
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
        self._hover = False
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

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = False
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
