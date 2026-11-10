"""应用图标（运行时绘制）。

设计：Win11 圆角方块 + 白色仪表盘。
仪表盘这个符号和「系统管家」对得上（监控/调优），而且在小尺寸下依然能认出来 ——
16px 的托盘图标里，细线条会糊成一团，所以全部用粗笔画。

只在这里画一次：窗口图标、托盘图标、以及打包用的 .ico 都由 `render()` 出图，
避免出现「exe 图标和界面里图标不一样」的情况。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
)

# 背景渐变：上浅下深，和 Win11 应用图标的光照方向一致
TOP = "#2A8BE0"
BOTTOM = "#0B5AA8"


def render(size: int) -> QImage:
    """画一张 size×size 的图标。"""
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)

    s = float(size)
    # 圆角方块：留一点边距，圆角半径按 Win11 的 22% 走
    inset = s * 0.02
    box = QRectF(inset, inset, s - inset * 2, s - inset * 2)
    gradient = QLinearGradient(box.topLeft(), box.bottomLeft())
    gradient.setColorAt(0.0, QColor(TOP))
    gradient.setColorAt(1.0, QColor(BOTTOM))
    painter.setPen(Qt.NoPen)
    painter.setBrush(gradient)
    painter.drawRoundedRect(box, s * 0.22, s * 0.22)

    # 仪表盘：240° 圆弧，缺口朝下
    cx, cy = s * 0.5, s * 0.545
    radius = s * 0.265
    arc = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
    pen = QPen(QColor("#FFFFFF"), max(1.6, s * 0.095))
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    # Qt 角度：0° 在 3 点方向，正数为逆时针。从 210° 顺时针扫 240°
    painter.drawArc(arc, 210 * 16, -240 * 16)

    # 指针：指向右上，长度约为半径的 0.72
    angle = 58.0
    length = radius * 0.72
    import math

    rad = math.radians(angle)
    tip = QPointF(cx + length * math.cos(rad), cy - length * math.sin(rad))
    pen = QPen(QColor("#FFFFFF"), max(1.6, s * 0.075))
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.drawLine(QPointF(cx, cy), tip)

    # 轴心：小尺寸下省掉，否则会和指针糊在一起
    if size >= 32:
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(QPointF(cx, cy), s * 0.045, s * 0.045)

    painter.end()
    return image
