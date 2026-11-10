"""主题与全局样式（Windows 11 Fluent）。

一套主题 = 一份调色板 + 一个强调色。配色全部取自 Win11 语义色，不自定义品牌色。

和上一版的区别：
- 从「6 套自定义配色」改成「浅色 / 深色 / 跟随系统」三档
- 强调色读 Windows 系统强调色，深色模式下自动提亮保证对比度
- 所有颜色仍然从模块全局变量取，页面里不要写死十六进制

切换靠 `set_theme()` 改全局变量，再由主窗口重建界面 —— 很多控件的颜色是在
构造时写进内联样式表的，光改变量不会让它们变色。
"""

from __future__ import annotations

from dataclasses import dataclass

FONT = '"Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", sans-serif'
SIDEBAR_WIDTH = 228

MODES = ("system", "dark", "light")
MODE_NAMES = {"system": "跟随系统", "dark": "深色", "light": "浅色"}

FALLBACK_ACCENT = "#0078D4"


@dataclass(frozen=True)
class Theme:
    key: str
    name: str
    dark: bool
    bg: str
    sidebar: str
    panel: str
    panel_alt: str
    panel_sunken: str
    hover: str
    border: str
    border_soft: str
    border_strong: str
    text: str
    text_sub: str
    text_dim: str
    accent: str
    accent_soft: str
    accent_hover: str
    accent_pressed: str
    on_accent: str
    red: str
    amber: str
    green: str
    cyan: str
    purple: str
    track: str
    knob: str
    scroll: str
    scroll_hover: str
    row_alt: str
    toast_bg: str
    dot_neutral: str


# ---------------------------------------------------------------- 颜色工具


def _to_rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*[max(0, min(255, int(round(v)))) for v in rgb])


def mix(a: str, b: str, t: float) -> str:
    """把 a 往 b 混合 t（0~1）。"""
    ra, ga, ba = _to_rgb(a)
    rb, gb, bb = _to_rgb(b)
    return _to_hex(
        (ra + (rb - ra) * t, ga + (gb - ga) * t, ba + (bb - ba) * t)
    )


def system_accent() -> str:
    """读 Windows 系统强调色。读不到就用 Win11 默认蓝。"""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "ColorizationColor")
        # 是 ARGB，高位是透明度，取低 24 位
        value = int(value) & 0xFFFFFF
        return _to_hex(((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF))
    except Exception:
        return FALLBACK_ACCENT


def system_is_dark() -> bool:
    """系统当前是不是深色模式。

    读不到时按浅色处理：Windows 出厂默认是浅色，而回落到深色会让
    「跟随系统」看起来像固定在深色，用户第一反应是设置没生效。
    """
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return int(value) == 0
    except Exception:
        return False


# ---------------------------------------------------------------- 调色板


def _build(dark: bool, accent_base: str) -> Theme:
    if dark:
        # 深色下把强调色往白里提，否则在 #2B2B2B 上对比度不够
        accent = mix(accent_base, "#FFFFFF", 0.34)
        return Theme(
            key="dark",
            name="深色",
            dark=True,
            bg="#202020",
            sidebar="#272727",
            panel="#2B2B2B",
            panel_alt="#323232",
            panel_sunken="#1F1F1F",
            hover="#383838",
            border="#3D3D3D",
            border_soft="#343434",
            border_strong="#4A4A4A",
            text="#FFFFFF",
            text_sub="#D6D6D6",
            text_dim="#9A9A9A",
            accent=accent,
            accent_soft=mix("#2B2B2B", accent, 0.18),
            accent_hover=mix(accent, "#FFFFFF", 0.16),
            accent_pressed=mix(accent, "#000000", 0.14),
            on_accent=mix(accent, "#000000", 0.82),
            red="#FF99A4",
            amber="#F2C14E",
            green="#6CCB5F",
            cyan="#7DB8FF",
            purple="#C3A0FF",
            track="#3D3D3D",
            knob="#FFFFFF",
            scroll="#4A4A4A",
            scroll_hover="#5E5E5E",
            # Win11 表格不隔行变色，这里保持一致
            row_alt="#2B2B2B",
            toast_bg="#2B2B2B",
            dot_neutral="#5E5E5E",
        )

    accent = mix(accent_base, "#000000", 0.08)
    return Theme(
        key="light",
        name="浅色",
        dark=False,
        bg="#F3F3F3",
        sidebar="#F9F9F9",
        panel="#FFFFFF",
        panel_alt="#F7F7F7",
        panel_sunken="#EFEFEF",
        hover="#F0F0F0",
        border="#E3E3E3",
        border_soft="#ECECEC",
        border_strong="#D6D6D6",
        text="#1A1A1A",
        text_sub="#5D5D5D",
        text_dim="#8B8B8B",
        accent=accent,
        accent_soft=mix("#FFFFFF", accent, 0.10),
        accent_hover=mix(accent, "#000000", 0.12),
        accent_pressed=mix(accent, "#000000", 0.24),
        on_accent="#FFFFFF",
        red="#D13438",
        amber="#C07C00",
        green="#107C10",
        cyan="#0078D4",
        purple="#8764B8",
        track="#E3E3E3",
        knob="#FFFFFF",
        scroll="#C7C7C7",
        scroll_hover="#A8A8A8",
        row_alt="#FFFFFF",
        toast_bg="#FFFFFF",
        dot_neutral="#C7C7C7",
    )


THEMES: dict[str, Theme] = {}
THEME_ORDER = MODES
DEFAULT_THEME = "system"

_current: Theme = _build(True, FALLBACK_ACCENT)
_mode = "system"
_accent_base = FALLBACK_ACCENT

# 下面这些是给页面用的全局变量，由 _publish 赋值
BG = SIDEBAR = PANEL = PANEL_ALT = PANEL_SUNKEN = HOVER = ""
BORDER = BORDER_SOFT = BORDER_STRONG = ""
TEXT = TEXT_SUB = TEXT_DIM = ""
ACCENT = ACCENT_SOFT = ACCENT_HOVER = ACCENT_PRESSED = ON_ACCENT = ""
RED = AMBER = GREEN = CYAN = PURPLE = BLUE = ""
TRACK = KNOB = SCROLL = SCROLL_HOVER = ROW_ALT = TOAST_BG = DOT_NEUTRAL = ""


def _publish(theme: Theme) -> None:
    global _current
    global BG, SIDEBAR, PANEL, PANEL_ALT, PANEL_SUNKEN, HOVER
    global BORDER, BORDER_SOFT, BORDER_STRONG
    global TEXT, TEXT_SUB, TEXT_DIM
    global ACCENT, ACCENT_SOFT, ACCENT_HOVER, ACCENT_PRESSED, ON_ACCENT
    global RED, AMBER, GREEN, CYAN, PURPLE, BLUE
    global TRACK, KNOB, SCROLL, SCROLL_HOVER, ROW_ALT, TOAST_BG, DOT_NEUTRAL

    _current = theme
    THEMES[theme.key] = theme
    BG = theme.bg
    SIDEBAR = theme.sidebar
    PANEL = theme.panel
    PANEL_ALT = theme.panel_alt
    PANEL_SUNKEN = theme.panel_sunken
    HOVER = theme.hover
    BORDER = theme.border
    BORDER_SOFT = theme.border_soft
    BORDER_STRONG = theme.border_strong
    TEXT = theme.text
    TEXT_SUB = theme.text_sub
    TEXT_DIM = theme.text_dim
    ACCENT = theme.accent
    ACCENT_SOFT = theme.accent_soft
    ACCENT_HOVER = theme.accent_hover
    ACCENT_PRESSED = theme.accent_pressed
    ON_ACCENT = theme.on_accent
    RED = theme.red
    AMBER = theme.amber
    GREEN = theme.green
    CYAN = theme.cyan
    PURPLE = theme.purple
    BLUE = theme.accent
    TRACK = theme.track
    KNOB = theme.knob
    SCROLL = theme.scroll
    SCROLL_HOVER = theme.scroll_hover
    ROW_ALT = theme.row_alt
    TOAST_BG = theme.toast_bg
    DOT_NEUTRAL = theme.dot_neutral


def set_theme(mode: str) -> bool:
    """切换主题模式（system / dark / light），返回是否真的变了。

    传旧的皮肤名（midnight / daylight 之类）会自动映射过来，保证老配置能用。
    """
    global _mode
    mode = _resolve(mode)
    dark = system_is_dark() if mode == "system" else (mode == "dark")
    if mode == _mode and _current.dark == dark:
        return False
    _mode = mode
    _publish(_build(dark, _accent_base))
    return True


def set_accent(color: str | None = None) -> bool:
    """设置强调色。传 None 表示跟随系统。"""
    global _accent_base
    target = (color or system_accent()).upper()
    if target == _accent_base:
        return False
    _accent_base = target
    _publish(_build(_current.dark, _accent_base))
    return True


def _resolve(key: str) -> str:
    if key in MODES:
        return key
    # 旧皮肤名映射。认不出来的值一律退回「跟随系统」—— 这里原来回落到 dark，
    # 于是配置里只要有一个拼错的值，程序就永久固定在深色，而设置页显示的
    # 却是「跟随系统」，看上去像系统设置没生效。
    if key in {"daylight", "paper", "light"}:
        return "light"
    if key in {"midnight", "night", "dark"}:
        return "dark"
    return "system"


def current() -> Theme:
    return _current


def current_mode() -> str:
    return _mode


def current_accent_base() -> str:
    return _accent_base


def is_dark() -> bool:
    return _current.dark


def with_alpha(color: str, alpha: float) -> str:
    """把 #RRGGBB 转成 rgba()，用于描边、分隔线这类需要半透明的地方。"""
    r, g, b = _to_rgb(color)
    return f"rgba({r}, {g}, {b}, {alpha:.2f})"


def load_color(percent: float) -> str:
    """占用越高越警戒。"""
    if percent >= 85:
        return RED
    if percent >= 65:
        return AMBER
    return ACCENT


def tip_style() -> str:
    """提示框的声明块（不带选择器）。

    自绘提示框直接把这串设成自己的样式表 —— 控件自己的样式表对自己一定生效，
    不用赌 QSS 选择器能不能匹配上自定义类。
    """
    return (
        f"background-color: {TOAST_BG}; color: {TEXT};"
        f" border: 1px solid {BORDER_STRONG}; border-radius: 4px;"
        " padding: 6px 9px; font-size: 12px;"
    )


def tooltip_qss() -> str:
    """给 Qt 自带提示框兜底的规则（触发控件没有样式表时才起作用）。"""
    return f"QToolTip {{ {tip_style()} }}"


_publish(_build(True, FALLBACK_ACCENT))


# ---------------------------------------------------------------- 全局样式


def app_stylesheet() -> str:
    # 顶部「一键优化」用柔和版（淡强调色底 + 强调色字），页面自己的主按钮才留实心蓝。
    # 一屏两个实心蓝按钮并排，谁也说不清哪个更重要。
    tonal = mix(PANEL, ACCENT, 0.22 if is_dark() else 0.12)
    tonal_hover = mix(PANEL, ACCENT, 0.32 if is_dark() else 0.20)
    tonal_pressed = mix(PANEL, ACCENT, 0.16 if is_dark() else 0.08)
    tonal_line = with_alpha(ACCENT, 0.36 if is_dark() else 0.30)
    # 强调色当文字压在淡强调色底上只有 3.7:1，往正文色再拉一档才够读
    tonal_text = mix(ACCENT, TEXT, 0.30)
    return f"""
    QWidget {{
        font-family: {FONT};
        font-size: 13px;
        color: {TEXT};
    }}
    QMainWindow, #Root, #PageArea {{
        background: {BG};
    }}
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}

    QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {SCROLL}; border-radius: 4px; min-height: 28px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {SCROLL_HOVER}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        height: 0; background: none;
    }}
    QScrollBar:horizontal {{ height: 0px; }}

    /* Win11 次按钮：描边 + 底部加深 1px */
    QPushButton {{
        background: {PANEL};
        border: 1px solid {BORDER};
        border-bottom-color: {BORDER_STRONG};
        border-radius: 4px;
        padding: 5px 14px;
        color: {TEXT};
        font-size: 13px;
        min-height: 22px;
    }}
    QPushButton:hover {{ background: {HOVER}; }}
    QPushButton:pressed {{ background: {PANEL_SUNKEN}; color: {TEXT_SUB}; }}
    QPushButton:disabled {{ color: {TEXT_DIM}; background: {PANEL_SUNKEN};
        border-color: {BORDER_SOFT}; }}

    QPushButton#Primary {{
        background: {ACCENT};
        border: 1px solid {ACCENT};
        border-radius: 4px;
        color: {ON_ACCENT};
        font-size: 13px;
        font-weight: 600;
        padding: 0 16px;
        min-height: 30px;
    }}
    QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
    QPushButton#Primary:pressed {{ background: {ACCENT_PRESSED}; border-color: {ACCENT_PRESSED}; }}
    /* 不可点的主按钮走灰，别用淡蓝：淡蓝现在专指「可点的全局动作」 */
    QPushButton#Primary:disabled {{ background: {PANEL_SUNKEN}; color: {TEXT_DIM};
        border-color: transparent; }}

    /* 全局动作：淡强调色底，和页面主按钮的实心蓝拉开一档 */
    QPushButton#Tonal {{
        background: {tonal};
        border: 1px solid {tonal_line};
        border-radius: 4px;
        color: {tonal_text};
        font-size: 13px;
        font-weight: 600;
        padding: 0 16px;
        min-height: 30px;
    }}
    QPushButton#Tonal:hover {{ background: {tonal_hover}; border-color: {ACCENT}; }}
    QPushButton#Tonal:pressed {{ background: {tonal_pressed}; border-color: {ACCENT}; }}
    QPushButton#Tonal:disabled {{ background: {PANEL_SUNKEN}; color: {TEXT_DIM};
        border-color: {BORDER_SOFT}; }}

    QPushButton#Ghost {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: 4px;
        color: {TEXT_SUB};
        padding: 0 10px;
        min-height: 26px;
        font-size: 12px;
    }}
    QPushButton#Ghost:hover {{ background: {HOVER}; color: {TEXT}; }}
    QPushButton#Ghost:pressed {{ background: {PANEL_SUNKEN}; }}

    /* 行内「去处理 ›」：不带底的文字按钮。体检页的不达标项和建议行用它，
     min-height 必须能塞进 30px 的因子行，所以比 Ghost 还矮一档 */
    QPushButton#Link {{
        background: transparent;
        border: none;
        border-radius: 3px;
        color: {ACCENT};
        padding: 0 3px;
        min-height: 18px;
        font-size: 10.5px;
    }}
    QPushButton#Link:hover {{ background: {ACCENT_SOFT}; color: {tonal_text}; }}
    QPushButton#Link:pressed {{ background: {PANEL_SUNKEN}; }}

    QLineEdit {{
        background: {PANEL};
        border: 1px solid {BORDER};
        border-bottom-color: {BORDER_STRONG};
        border-radius: 4px;
        padding: 0 10px;
        min-height: 28px;
        color: {TEXT};
        selection-background-color: {ACCENT};
        selection-color: {ON_ACCENT};
    }}
    QLineEdit:focus {{ border-bottom-color: {ACCENT}; border-color: {BORDER_STRONG}; }}

    QComboBox {{
        background: {PANEL};
        border: 1px solid {BORDER};
        border-bottom-color: {BORDER_STRONG};
        border-radius: 4px;
        padding: 0 26px 0 10px;
        min-height: 28px;
        color: {TEXT};
        font-size: 12.5px;
    }}
    QComboBox:hover {{ background: {HOVER}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background: {PANEL_ALT};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 4px;
        color: {TEXT};
        outline: none;
        selection-background-color: {ACCENT_SOFT};
        selection-color: {TEXT};
    }}

    QTableWidget {{
        background: transparent;
        border: none;
        gridline-color: transparent;
        outline: none;
        selection-background-color: {ACCENT_SOFT};
        alternate-background-color: {ROW_ALT};
    }}
    QTableWidget::item {{
        border: none;
        color: {TEXT};
        padding: 0;
    }}
    QTableWidget::item:selected {{
        background: {ACCENT_SOFT};
        color: {TEXT};
    }}
    QHeaderView {{ background: transparent; border: none; }}
    QHeaderView::section {{
        background: transparent;
        border: none;
        border-bottom: 1px solid {BORDER};
        color: {TEXT_DIM};
        font-size: 11.5px;
        font-weight: 600;
        padding: 0 4px 8px 4px;
    }}
    QHeaderView::section:first {{ padding-left: 8px; }}
    QTableCornerButton::section {{ background: transparent; border: none; }}

    QMenu {{
        background: {PANEL_ALT};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 5px;
    }}
    QMenu::item {{
        padding: 7px 20px 7px 12px;
        border-radius: 4px;
        font-size: 12.5px;
        color: {TEXT};
    }}
    QMenu::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}
    QMenu::item:disabled {{ color: {TEXT_DIM}; }}
    QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}

    /* 只是兜底：真正在用的是 ui/tooltip.py 自绘的那个 */
    {tooltip_qss()}

    QSlider::groove:horizontal {{
        height: 3px; background: {TRACK}; border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
    QSlider::handle:horizontal {{
        background: {KNOB}; border: 1px solid {BORDER_STRONG};
        width: 12px; height: 12px; margin: -5px 0; border-radius: 6px;
    }}

    QCheckBox {{ color: {TEXT_SUB}; font-size: 12.5px; spacing: 8px; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px; border-radius: 4px;
        border: 1px solid {BORDER_STRONG}; background: {PANEL};
    }}
    QCheckBox::indicator:checked {{
        background: {ACCENT}; border-color: {ACCENT};
    }}

    QListWidget {{
        background: {PANEL};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 4px;
        font-size: 12.5px;
        outline: none;
    }}
    QListWidget::item {{ padding: 6px 8px; border-radius: 4px; }}
    QListWidget::item:hover {{ background: {HOVER}; }}
    QListWidget::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}

    QSplitter::handle {{ background: {BORDER}; }}
    """
