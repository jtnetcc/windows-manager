"""应用图标（运行时绘制）。

设计：深蓝底 + 盾牌轮廓 + 一根带缝的内存条。
为什么是这个组合：盾是「管家 / 守护」最直接的语义，内存条指业务（管内存），
两者一外一内、一空一实，同一条竖中轴对齐，128px 有主次，16px 还剩下
「盾里一块亮」的轮廓 —— 比取景框那版更容易被认成「防护类工具」。

早先试过并放弃的方案：
· 纯仪表盘：指针比弧细一圈，缩到 16px 糊成一个 C；
· 白条 + 青绿颗粒：三种颜色四个元素，小尺寸里搅成一团；
· 取景框 + 内存条：语义偏「扫描 / 定位」，和清理、防护的关系不够直接。

只在这里画一次：窗口图标、托盘图标、以及打包用的 .ico 都由 `render()` 出图，
避免出现「exe 图标和界面里图标不一样」的情况。
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)

# 背景渐变：左上浅、右下深
TOP = "#2B4C7E"
BOTTOM = "#0A1220"
MARK = QColor("#FFFFFF")

# 盾牌轮廓（按 256 画布归一化）：肩宽、颈线高度、底尖高度、笔宽
SHOULDER = 0.275  # 左右肩到中线的距离
TOP_Y = 0.175
NECK_Y = 0.285
BELT_Y = 0.505
TIP_Y = 0.815
STROKE = 0.048
STROKE_MIN = 1.6  # 小尺寸的笔画下限（像素）。1.25 时 16px 的盾肩上沿会断成虚线

# 内存条：宽、高、圆角、缝宽、缝数
BAR_W = 0.335
BAR_W_SMALL = 0.30  # 16~24px 收窄一点，笔画加粗后左右本来就没剩几像素
BAR_H = 0.128
BAR_R = 0.018
BAR_CY = 0.462
SLIT_W = 0.020
SLITS = 2


def _shield(s: float) -> QPainterPath:
    """盾牌轮廓：上方平肩、腰间收进、下方收敛成圆尖。"""
    path = QPainterPath()
    cx = s * 0.5
    path.moveTo(cx, s * TOP_Y)
    path.lineTo(s * (0.5 + SHOULDER), s * NECK_Y)
    path.lineTo(s * (0.5 + SHOULDER), s * BELT_Y)
    path.quadTo(s * (0.5 + SHOULDER), s * 0.725, cx, s * TIP_Y)
    path.quadTo(s * (0.5 - SHOULDER), s * 0.725, s * (0.5 - SHOULDER), s * BELT_Y)
    path.lineTo(s * (0.5 - SHOULDER), s * NECK_Y)
    path.closeSubpath()
    return path


def _memory_bar(s: float) -> QPainterPath:
    """中间的白色内存条。缝用路径相减挖出来，而不是叠一块底色 ——
    叠底色会在渐变上留下一条明显色差，缩小后尤其脏。"""
    path = QPainterPath()
    width = BAR_W if s >= 32 else BAR_W_SMALL
    left = (0.5 - width / 2) * s
    top = (BAR_CY - BAR_H / 2) * s
    path.addRoundedRect(QRectF(left, top, width * s, BAR_H * s), BAR_R * s, BAR_R * s)

    # 缝本身也要能在像素网格上站住。16~24px 时缝不足 1px，挖出来只会是一层
    # 灰雾，不如整块实心 —— 那时「盾里有个亮块」就是全部信息量。
    slits = SLITS if s >= 32 else 0
    slit_px = max(1.0, s * SLIT_W)
    for k in range(slits):
        x = left + width * s * (k + 1) / (slits + 1)
        slit = QPainterPath()
        slit.addRect(QRectF(x - slit_px / 2, top - 1, slit_px, BAR_H * s + 2))
        path = path.subtracted(slit)
    return path


def render(size: int) -> QImage:
    """画一张 size×size 的图标。"""
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)

    s = float(size)

    # 圆角方块底
    inset = s * 0.02
    box = QRectF(inset, inset, s - inset * 2, s - inset * 2)
    gradient = QLinearGradient(box.topLeft(), box.bottomRight())
    gradient.setColorAt(0.0, QColor(TOP))
    gradient.setColorAt(1.0, QColor(BOTTOM))
    painter.setPen(Qt.NoPen)
    painter.setBrush(gradient)
    painter.drawRoundedRect(box, s * 0.215, s * 0.215)

    # 一圈极淡的内高光，压住深色边缘在浅色桌面上「糊」的感觉
    painter.setPen(QPen(QColor(255, 255, 255, 26), max(1.0, s * 0.006)))
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(
        box.adjusted(s * 0.006, s * 0.006, -s * 0.006, -s * 0.006),
        s * 0.21,
        s * 0.21,
    )

    # 盾牌轮廓
    pen = QPen(MARK, max(s * STROKE, STROKE_MIN))
    pen.setJoinStyle(Qt.RoundJoin)
    pen.setCapStyle(Qt.FlatCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(_shield(s))

    # 中间的内存条
    painter.setPen(Qt.NoPen)
    painter.setBrush(MARK)
    painter.drawPath(_memory_bar(s))

    painter.end()
    return image
