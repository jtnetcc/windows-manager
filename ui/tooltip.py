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
        self._text = ""
        self._anchor = QPoint()
        self._font = QFont(self.font())
        self._font.setPixelSize(12)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_text(self, text: str, global_pos: QPoint) -> None:
        if not text:
            self.hide()
            return
        self._text = text
        metrics = QFontMetrics(self._font)
        # 先按最大宽度折行量一遍：长路径自动换行，短文本不会被撑成一条
        bounds = metrics.boundingRect(
            QRect(0, 0, MAX_WIDTH, 10000),
            Qt.TextWordWrap | Qt.AlignLeft,
            text,
        )
        self.resize(bounds.width() + PAD_X * 2, bounds.height() + PAD_Y * 2)

        # 右下偏一点，别正好压在鼠标下面；贴到屏幕边缘时往回收
        pos = global_pos + QPoint(14, 20)
        screen = QApplication.screenAt(global_pos) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        pos.setX(max(area.left() + 2, min(pos.x(), area.right() - self.width() - 4)))
        pos.setY(max(area.top() + 2, min(pos.y(), area.bottom() - self.height() - 4)))
        self.move(pos)

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
            Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignVCenter,
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
            if tip:
                return str(tip)

    widget: QWidget | None = obj
    while widget is not None:
        tip = widget.toolTip()
        if tip:
            return tip
        widget = widget.parentWidget()
    return ""
