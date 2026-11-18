"""悬停提示回归：尺寸、边界、层级、触发范围。

覆盖这次修的四类问题：
1. 量尺寸用的宽度和真正绘制的宽度不一致 → 长文本最后一行被裁；
2. 贴屏幕右下角时提示框跑出可用区 → 只看见一半；
3. 提示框挡住底下的表格 → 鼠标滑过去点不到；
4. 表格空白处顺着父链找到表格级 tooltip → 弹出与位置无关的说明。

用法：python tools/check_tooltip.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QFontMetrics  # noqa: E402
from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem  # noqa: E402

from ui import tooltip as tt  # noqa: E402


LONG = (
    "列表合并两个来源：注册表登记的安装包 + 安装目录里带卸载程序的软件\n"
    "（网游客户端、绿色版常只占后者）。卸载一律打开程序自带的向导，\n"
    "本工具不会替你点「下一步」，也不会在后台替你确认任何步骤。"
)


def check_bounds(app: QApplication) -> None:
    tip = tt.Tip()
    screen = app.primaryScreen()
    area = screen.availableGeometry()

    for where in (
        QPoint(area.left() + 3, area.top() + 3),
        QPoint(area.right() - 3, area.bottom() - 3),
    ):
        tip.show_text(LONG, where)
        box = tip.geometry()
        assert area.contains(box), f"提示框跑出屏幕：{box} vs {area}"
        print(f"边界 ✓（锚点 {where.x()},{where.y()} → {box.width()}×{box.height()} 全在屏内）")

    # 绘制区必须装得下按同一套 flags 量出来的文本
    inside = tip.rect().adjusted(tt.PAD_X, tt.PAD_Y, -tt.PAD_X, -tt.PAD_Y)
    flags = Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop
    need = QFontMetrics(tip._font).boundingRect(inside, flags, LONG)
    assert need.height() <= inside.height(), f"文本仍被裁：需要 {need.height()} > {inside.height()}"
    assert tip.testAttribute(Qt.WA_TransparentForMouseEvents), "提示框会吃掉鼠标事件"
    print("尺寸/层级 ✓（绘制区够高，鼠标事件穿透）")

    tip.show_text("   ", QPoint(100, 100))
    assert not tip.isVisible(), "空白文本没有收起提示框"
    print("空文本 ✓")


def check_trigger_scope() -> None:
    table = QTableWidget(2, 2)
    table.setToolTip("表格级说明，不该在空白处弹出来")
    table.setItem(0, 0, QTableWidgetItem("A"))
    table.item(0, 0).setToolTip("单元格说明")

    view = table.viewport()
    center = table.visualRect(table.model().index(0, 0)).center()
    assert tt.text_at(view, center) == "单元格说明", "单元格 tooltip 丢了"

    blank = QPoint(5, table.height() - 3)
    assert tt.text_at(view, blank) == "", f"空白处弹出了多余的提示：{tt.text_at(view, blank)!r}"
    print("触发范围 ✓（单元格有提示，空白处不弹）")


def main() -> int:
    app = QApplication(sys.argv[:1])
    check_bounds(app)
    check_trigger_scope()
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
