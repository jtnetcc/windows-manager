"""自绘 tooltip。

为什么不用 Qt 自带的：`QToolTip` 是带 ToolTip 标志的独立顶层窗口，样式表规则
经常被「触发控件自己的样式表」挡掉。本项目几乎每个 QLabel 都在 setStyleSheet 里
写了 `color:`，结果全局 QSS 的 QToolTip 规则不生效，底色回落到系统调色板——
Windows 深色模式 + 应用浅色主题时就是纯黑底配 #1A1A1A 的字，整块看不见字。

自己画一个反而简单：背景、边框、圆角都在 paintEvent 里画，颜色跟着主题走，
不用赌样式表能不能匹配上。

接管方式：MainWindow 在应用上装事件过滤器，把 QEvent.ToolTip 换成这里的
`Tip.show_text()`，并返回 True 把事件吞掉（否则 Qt 还会弹一个黑框）。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QAbstractItemView, QApplication, QWidget

from . import theme_dark as T

# 鼠标离开锚点这么远就收起。给几像素余量，手抖不会把提示框抖没
HIDE_DISTANCE = 8
HIDE_AFTER_MS = 12000

PAD_X = 9
PAD_Y = 6
MAX_WIDTH = 460      # 长路径自动折行，再宽就挡视线了
RADIUS = 4


class Tip(QWidget):
    """跟着主题走的轻量提示框，自绘。"""

    def __init__(self):
        super().__init__(
            None,
            Qt.ToolTip | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint,
        )
        # 圆角靠透明背景才露得出来
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        # 提示框不能吃掉鼠标事件：鼠标滑过去之后仍然要能点到底下的表格/按钮。
        # 位置偏右下 14/20 只能避开「刚弹出」那一刻，之后鼠标移动过来是常态。
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._text = ""
        self._anchor = QPoint()
        self._font = QFont(self.font())
        self._font.setPixelSize(12)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_text(self, text: str, global_pos: QPoint) -> None:
        if not text or not text.strip():
            self.hide()
            return
        self._text = text.rstrip()

        screen = QApplication.screenAt(global_pos) or QApplication.primaryScreen()
        area = screen.availableGeometry()

        # 量文本时要用「真正绘制的宽度」。以前按 MAX_WIDTH 量、绘制区却比它
        # 窄 18px（左右内边距），临界长度会多折一行，最后一行被裁掉。
        # 先把可用宽度夹到屏幕内，再按同一套 flags 量高度。
        max_w = max(160, min(MAX_WIDTH, area.width() - 16))
        paint_w = max(80, max_w - PAD_X * 2 - 2)
        flags = Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop
        metrics = QFontMetrics(self._font)
        bounds = metrics.boundingRect(
            QRect(0, 0, paint_w, max(64, area.height() - 16)), flags, self._text
        )
        width = min(bounds.width() + PAD_X * 2 + 2, area.width() - 8)
        height = min(bounds.height() + PAD_Y * 2 + 4, area.height() - 8)
        self.resize(max(48, width), max(28, height))

        # 先放右下；越界就翻到鼠标左侧 / 上方（比一味夹到边缘更不挡内容），
        # 最后再夹一次，保证整框都在屏幕可用区内。
        x = global_pos.x() + 14
        y = global_pos.y() + 20
        if x + self.width() > area.right() - 4:
            x = global_pos.x() - self.width() - 14
        if y + self.height() > area.bottom() - 4:
            y = global_pos.y() - self.height() - 20
        x = max(area.left() + 4, min(x, area.right() - self.width() - 4))
        y = max(area.top() + 4, min(y, area.bottom() - self.height() - 4))
        self.move(x, y)

        self._anchor = global_pos
        self.show()
        self.raise_()
        self._timer.start(HIDE_AFTER_MS)

    def should_hide_for(self, global_pos: QPoint) -> bool:
        """鼠标动多远才算「离开」。"""
        delta = global_pos - self._anchor
        return abs(delta.x()) > HIDE_DISTANCE or abs(delta.y()) > HIDE_DISTANCE

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        body = self.rect().adjusted(0, 0, -1, -1)
        painter.setBrush(QColor(T.TOAST_BG))
        painter.setPen(QPen(QColor(T.BORDER_STRONG), 1))
        painter.drawRoundedRect(body, RADIUS, RADIUS)

        painter.setPen(QColor(T.TEXT))
        painter.setFont(self._font)
        painter.drawText(
            body.adjusted(PAD_X, PAD_Y, -PAD_X, -PAD_Y),
            Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop,
            self._text,
        )


def text_at(obj: QWidget, pos: QPoint) -> str:
    """取鼠标所在处的提示文字。

    优先看表格/列表单元格（tooltip 挂在 item 的 ToolTipRole 上），
    再看控件自己，最后顺着父链找——子控件没设提示时，用户期望看到整行的说明。
    """
    view = obj
    if not isinstance(view, QAbstractItemView):
        parent = obj.parentWidget()
        view = parent if isinstance(parent, QAbstractItemView) else None
    if view is not None:
        index = view.indexAt(pos)
        if index.isValid():
            tip = index.data(Qt.ToolTipRole)
            return str(tip) if tip else ""
        # 表格/列表的空白处不弹提示。以前会顺着父链找到 view 自己的
        # toolTip（例如「点表头排序」），鼠标停在空白格上就冒出来一句
        # 和位置无关的说明，看起来就像提示内容不对/显示不全。
        return ""

    widget: QWidget | None = obj
    while widget is not None:
        tip = widget.toolTip()
        if tip:
            return tip
        widget = widget.parentWidget()
    return ""
