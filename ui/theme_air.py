"""留白皮肤（air）：参考稿那套「淡底 + 白卡片 + 大圆角」的样子。

**这是一版试验品，随时可以整块关掉。** 关掉的方式有三种，从轻到重：

1. 设置里 `"skin": "classic"`（默认就是它，等于没启用过）
2. 启动参数 `python main.py --skin classic`
3. 把 `main.py` 里那一行 `theme_dark.set_skin(...)` 删掉

它只做两件事：换一套配色 + 换一套样式表/圆角参数。页面代码一行没改，
所以关掉之后立刻回到原来的界面，不存在「改了一半回不去」。

设计取自参考图：
- 底色是极淡的青色渐变，不是纯白 —— 白卡片浮在上面才有层次
- 卡片 18px 圆角、白底、一圈几乎看不见的边（不画高光、不画切角）
- 侧栏选中项是一块 12px 圆角的淡灰药丸，没有左侧竖条
- 按钮 10px 圆角、无边框，靠底色区分层级
"""

from __future__ import annotations

from . import theme_dark as D

CARD_RADIUS = 18
NAV_RADIUS = 12
CONTENT_MARGIN = 28
TITLE_SIZE = 24

# 淡青底：上浅下稍深，模拟参考图那层极轻的渐变
BG_TOP = "#F1F8F8"
BG_BOTTOM = "#E9F1F3"


def _palette() -> D.Theme:
    # 用当前强调色（用户可能在设置里换过），不是写死的品牌色
    base = getattr(D, "_accent_base", D.BRAND_ACCENT) or D.BRAND_ACCENT
    accent = D.mix(base, "#000000", 0.34)
    return D.Theme(
        key="air",
        name="浅色（留白）",
        dark=False,
        bg=BG_TOP,
        sidebar="#F5FAFA",
        panel="#FFFFFF",
        panel_alt="#F7FBFB",
        panel_sunken="#E7EFEF",
        hover="#EFF5F5",
        border="#E8EFEF",
        border_soft="#F0F5F5",
        border_strong="#DCE5E5",
        text="#171C1E",
        text_sub="#576466",
        text_dim="#94A1A3",
        accent=accent,
        accent_soft=D.mix("#FFFFFF", accent, 0.10),
        accent_hover=D.mix(accent, "#000000", 0.14),
        accent_pressed=D.mix(accent, "#000000", 0.26),
        on_accent="#FFFFFF",
        red="#C9324B",
        amber="#9A5B00",
        green="#0E7A50",
        cyan="#0B7CB4",
        purple="#6B4CC0",
        track="#E3EAEA",
        knob="#FFFFFF",
        scroll="#CBD8D8",
        scroll_hover="#AFBEBE",
        row_alt="#FFFFFF",
        toast_bg="#FFFFFF",
        dot_neutral="#CBD8D8",
    )


def apply() -> None:
    """把皮肤参数推进 theme_dark，页面读全局变量就自动跟着变。"""
    D.CARD_RADIUS = CARD_RADIUS
    D.NAV_RADIUS = NAV_RADIUS
    D.NAV_BAR = False
    D.NAV_SELECTED = "#E9F0F0"
    D.CONTENT_MARGIN = CONTENT_MARGIN
    D.TITLE_SIZE = TITLE_SIZE
    D.SIDEBAR_BORDER = False
    D._publish(_palette())


def app_stylesheet() -> str:
    accent = D.ACCENT
    soft = D.ACCENT_SOFT
    return f"""
    QWidget {{
        font-family: {D.FONT};
        font-size: 13px;
        color: {D.TEXT};
    }}
    QMainWindow, #Root {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
            stop:0 {BG_TOP}, stop:1 {BG_BOTTOM});
    }}
    /* 内容区透明，让底色渐变透上来 —— 参考图是整块淡底，不是一个灰面板 */
    #PageArea {{ background: transparent; }}
    QDialog {{ background: {D.BG}; }}

    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}

    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 2px; }}
    QScrollBar::handle:vertical {{
        background: {D.SCROLL}; border-radius: 4px; min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {D.SCROLL_HOVER}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        height: 0; background: none;
    }}
    QScrollBar:horizontal {{ height: 0px; }}

    /* 按钮：无边框、大圆角，靠底色分层 */
    QPushButton {{
        background: {D.PANEL};
        border: 1px solid {D.BORDER};
        border-radius: 10px;
        padding: 6px 16px;
        color: {D.TEXT};
        font-size: 13px;
        min-height: 24px;
    }}
    QPushButton:hover {{ background: {D.HOVER}; }}
    QPushButton:pressed {{ background: {D.PANEL_SUNKEN}; }}
    QPushButton:disabled {{ color: {D.TEXT_DIM}; background: {D.PANEL_ALT};
        border-color: {D.BORDER_SOFT}; }}

    QPushButton#Primary {{
        background: {accent};
        border: 1px solid {accent};
        border-radius: 10px;
        color: #FFFFFF;
        font-weight: 600;
        padding: 0 18px;
        min-height: 32px;
    }}
    QPushButton#Primary:hover {{ background: {D.ACCENT_HOVER}; border-color: {D.ACCENT_HOVER}; }}
    QPushButton#Primary:pressed {{ background: {D.ACCENT_PRESSED}; border-color: {D.ACCENT_PRESSED}; }}
    QPushButton#Primary:disabled {{ background: {D.PANEL_SUNKEN}; color: {D.TEXT_DIM};
        border-color: transparent; }}

    QPushButton#Tonal {{
        background: {soft};
        border: 1px solid transparent;
        border-radius: 10px;
        color: {D.mix(accent, D.TEXT, 0.35)};
        font-weight: 600;
        padding: 0 18px;
        min-height: 32px;
    }}
    QPushButton#Tonal:hover {{ background: {D.mix(D.PANEL, accent, 0.20)}; }}
    QPushButton#Tonal:disabled {{ background: {D.PANEL_ALT}; color: {D.TEXT_DIM}; }}

    QPushButton#Ghost {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: 9px;
        color: {D.TEXT_SUB};
        padding: 0 10px;
        min-height: 26px;
        font-size: 12px;
    }}
    QPushButton#Ghost:hover {{ background: {D.HOVER}; color: {D.TEXT}; }}
    QPushButton#Ghost:pressed {{ background: {D.PANEL_SUNKEN}; }}

    QPushButton#Link {{
        background: transparent; border: none; border-radius: 6px;
        color: {accent}; padding: 0 4px; min-height: 18px; font-size: 10.5px;
    }}
    QPushButton#Link:hover {{ background: {soft}; }}
    QPushButton#Link:pressed {{ background: {D.PANEL_SUNKEN}; }}

    QLineEdit, QComboBox, QSpinBox {{
        background: {D.PANEL};
        border: 1px solid {D.BORDER};
        border-radius: 10px;
        padding: 0 12px;
        min-height: 30px;
        color: {D.TEXT};
        selection-background-color: {accent};
        selection-color: #FFFFFF;
    }}
    QLineEdit:focus {{ border-color: {accent}; }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox QAbstractItemView {{
        background: {D.PANEL};
        border: 1px solid {D.BORDER};
        border-radius: 10px;
        padding: 6px;
        color: {D.TEXT};
        outline: none;
        selection-background-color: {D.HOVER};
        selection-color: {D.TEXT};
    }}

    /* 表格：去掉网格线，只留一条极淡的行分隔 */
    QTableWidget {{
        background: transparent;
        border: none;
        gridline-color: transparent;
        outline: none;
    }}
    QTableWidget::item {{ padding: 4px 8px; border: none; }}
    QTableWidget::item:selected {{ background: {soft}; color: {D.TEXT}; }}
    QHeaderView {{ background: transparent; border: none; }}
    QHeaderView::section {{
        background: transparent;
        border: none;
        border-bottom: 1px solid {D.BORDER};
        padding: 6px 8px;
        color: {D.TEXT_DIM};
        font-size: 11px;
    }}
    QTableCornerButton::section {{ background: transparent; border: none; }}

    QListWidget {{
        background: {D.PANEL};
        border: 1px solid {D.BORDER};
        border-radius: 12px;
        padding: 6px;
        font-size: 12.5px;
        outline: none;
    }}
    QListWidget::item {{ padding: 7px 10px; border-radius: 8px; }}
    QListWidget::item:hover {{ background: {D.HOVER}; }}
    QListWidget::item:selected {{ background: {D.NAV_SELECTED}; color: {D.TEXT}; }}

    QProgressBar {{
        background: {D.TRACK};
        border: none;
        border-radius: 4px;
        height: 6px;
        text-align: center;
    }}
    QProgressBar::chunk {{ background: {accent}; border-radius: 4px; }}

    QCheckBox {{ color: {D.TEXT}; spacing: 8px; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px; border-radius: 5px;
        border: 1px solid {D.BORDER_STRONG}; background: {D.PANEL};
    }}
    QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; }}

    QSplitter::handle {{ background: {D.BORDER}; }}
    """
