"""主题与全局样式。

一套主题 = 一份调色板 + 一个强调色。

配色方向是「仪器面板」而不是「办公软件」：
- 底色压暗并向蓝偏，面板才浮得起来；纯中性灰（#202020 那一套）读起来像后台网页。
- 强调色固定为品牌青 `#2DD4BF`，不再默认跟随 Windows 系统色 —— 用户壁纸是红的，
  整个软件就变红，品牌感无从谈起。想跟系统的仍可在设置里选。
- 语义色提纯：警示红偏粉、警告黄偏橙，和底色拉开距离。

和上一版的区别：
- 强调色默认品牌色，「跟随系统」变成需要主动选择的选项（accent = "system"）
- 深色底从 #202020 换成 #0A0E13，面板 #121A23
- 面板边框改由 `panel_edge()` 派生，带一点冷青，不再是中性灰线

切换靠 `set_theme()` 改全局变量，再由主窗口重建界面 —— 很多控件的颜色是在
构造时写进内联样式表的，光改变量不会让它们变色。
"""

from __future__ import annotations

from dataclasses import dataclass

FONT = '"Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", sans-serif'
SIDEBAR_WIDTH = 228
# 收起态只留图标轨道：32px 导航行 + 左右各 17px 呼吸位
SIDEBAR_WIDTH_COMPACT = 66

MODES = ("system", "dark", "light")
MODE_NAMES = {"system": "跟随系统", "dark": "深色", "light": "浅色"}

# 品牌强调色。深色下往白里提亮、浅色下往黑里压深，同一颗种子两套都成立。
BRAND_ACCENT = "#2DD4BF"
# 读不到系统强调色时的兜底，和品牌色一致
FALLBACK_ACCENT = BRAND_ACCENT


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
        # 深色下把强调色往白里提，否则在 #121A23 上对比度不够
        accent = mix(accent_base, "#FFFFFF", 0.26)
        return Theme(
            key="dark",
            name="深色",
            dark=True,
            bg="#0A0E13",
            sidebar="#0D131A",
            panel="#121A23",
            panel_alt="#16202B",
            panel_sunken="#0C1219",
            hover="#1B2733",
            border="#1E2B38",
            border_soft="#182431",
            border_strong="#2C3E4F",
            text="#E8F3F2",
            text_sub="#9DB2BF",
            text_dim="#6A818F",
            accent=accent,
            accent_soft=mix("#121A23", accent, 0.17),
            accent_hover=mix(accent, "#FFFFFF", 0.14),
            accent_pressed=mix(accent, "#000000", 0.16),
            on_accent=mix(accent, "#000000", 0.84),
            red="#FF5C7A",
            amber="#FFB13D",
            green="#35D69A",
            cyan="#45B8F5",
            purple="#A78BFA",
            track="#1E2B38",
            knob="#E8F3F2",
            scroll="#2C3E4F",
            scroll_hover="#3C5364",
            # Win11 表格不隔行变色，这里保持一致
            row_alt="#121A23",
            toast_bg="#16202B",
            dot_neutral="#55697A",
        )

    # 浅色下强调色要压深：白底上 #2DD4BF 只有 1.8:1，当文字读不清
    accent = mix(accent_base, "#000000", 0.32)
    return Theme(
        key="light",
        name="浅色",
        dark=False,
        bg="#EFF4F6",
        sidebar="#F7FAFB",
        panel="#FFFFFF",
        panel_alt="#F4F8FA",
        panel_sunken="#E9F0F3",
        hover="#EAF1F4",
        border="#DCE6EB",
        border_soft="#E6EEF2",
        border_strong="#C6D6DE",
        text="#0D1A20",
        text_sub="#47606D",
        text_dim="#7B909C",
        accent=accent,
        accent_soft=mix("#FFFFFF", accent, 0.10),
        accent_hover=mix(accent, "#000000", 0.12),
        accent_pressed=mix(accent, "#000000", 0.24),
        on_accent="#FFFFFF",
        red="#C62B45",
        amber="#A65E00",
        green="#10794F",
        cyan="#0B7FB8",
        purple="#6D4FC4",
        track="#DCE6EB",
        knob="#FFFFFF",
        scroll="#C4D2DA",
        scroll_hover="#A6B8C2",
        row_alt="#FFFFFF",
        toast_bg="#FFFFFF",
        dot_neutral="#C4D2DA",
    )


THEMES: dict[str, Theme] = {}
THEME_ORDER = MODES
DEFAULT_THEME = "system"

_current: Theme = _build(True, FALLBACK_ACCENT)
_mode = "system"
_accent_base = BRAND_ACCENT

# ---------------------------------------------------------------- 皮肤
#
# 皮肤是「配色之外的那一层」：圆角多大、侧栏什么形态、面板画成切角还是圆角。
# 现在两套：
#   classic —— 原来的切角面板 + 紧凑信息密度（默认，回滚就是切回它）
#   air     —— 参考稿的留白风格：大圆角、白卡片、淡青底（ui/theme_air.py）
#
# 页面读的都是本模块的全局变量，所以切换皮肤只要重新发布一遍再重建界面，
# 不需要改任何页面代码。
SKIN = "classic"
CARD_RADIUS = 0          # 0 = 用切角（classic）；>0 = 圆角半径
NAV_RADIUS = 5
NAV_BAR = True           # 选中项左侧那条 3×16 的强调色竖条
NAV_SELECTED = ""        # 选中底色，空串 = 用 ACCENT_SOFT
SIDEBAR_BORDER = True    # 侧栏右侧是否画分隔线
CONTENT_MARGIN = 24      # 内容区左右留白
TITLE_SIZE = 19          # 页面大标题字号
CARD_PADDING = 0         # 0 = 各页自己传，>0 时统一覆盖


def set_skin(name: str) -> None:
    """切换皮肤。切换后必须整页重建才会生效（见 MainWindow._rebuild_for_theme）。"""
    global SKIN, CARD_RADIUS, NAV_RADIUS, NAV_BAR, NAV_SELECTED
    global CONTENT_MARGIN, TITLE_SIZE, CARD_PADDING, SIDEBAR_BORDER
    if name not in ("classic", "air"):
        name = "classic"
    SKIN = name
    if name == "air":
        from . import theme_air

        theme_air.apply()
        return
    # 切回经典：把圆角那几个开关恢复默认，再重新发布一次原配色
    CARD_RADIUS, NAV_RADIUS, NAV_BAR = 0, 5, True
    NAV_SELECTED, CONTENT_MARGIN, TITLE_SIZE = "", 24, 19
    SIDEBAR_BORDER = True
    CARD_PADDING = 0
    dark = system_is_dark() if _mode == "system" else (_mode == "dark")
    _publish(_build(dark, _accent_base))

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

    # 留白皮肤只有浅色一套：它改的不只是配色，还有圆角、留白和侧栏形态，
    # 强行做深色版等于再画一遍。切到它时优先于深浅选择。
    if SKIN == "air":
        if mode == _mode:
            return False
        _mode = mode
        from . import theme_air

        theme_air.apply()
        return True

    dark = system_is_dark() if mode == "system" else (mode == "dark")
    if mode == _mode and _current.dark == dark:
        return False
    _mode = mode
    _publish(_build(dark, _accent_base))
    return True


def set_accent(color: str | None = None) -> bool:
    """设置强调色。

    三种取值：
    - `None` / 空串 → 品牌色（默认）
    - `"system"`    → 跟随 Windows 系统强调色
    - `#RRGGBB`     → 指定颜色

    和上一版的区别：空串原来表示「跟随系统」，现在表示「用品牌色」。
    默认跟系统会让整个软件的色调被壁纸决定，科技感根本立不住。
    """
    global _accent_base
    raw = (color or "").strip()
    if not raw:
        target = BRAND_ACCENT
    elif raw.lower() == "system":
        target = system_accent()
    else:
        target = raw.upper()
    if target == _accent_base:
        return False
    _accent_base = target
    if SKIN == "air":
        from . import theme_air

        theme_air.apply()
        return True
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


def panel_edge() -> str:
    """切角面板的发丝线。

    往强调色混合，而不是往正文色混 —— 一条中性灰边只会让面板显得「普通」，
    带一点冷青的边才是仪表盘的感觉。浅色底上颜色本来就淡，
    不加重到 30% 根本看不见轮廓。
    """
    return mix(PANEL, ACCENT, 0.30 if not is_dark() else 0.18)


def mem_colors() -> tuple[str, str, str]:
    """内存构成的三段：已用 / 待命 / 空闲。

    不用红黄绿。内存占 60% 是正常状态，画成红灯会让整页一直像在报警；
    改用主色阶 —— 亮青 → 暗青 → 中性灰，量级照样一眼能分。
    第三段不用 TRACK：那是「空轨道」的色，当图例圆点会看不见。
    """
    return (ACCENT, mix(ACCENT, PANEL, 0.58), mix(TRACK, TEXT_DIM, 0.60))


def panel_edge_top() -> str:
    """面板顶边高光线。比发丝线再亮一档，模拟从上方打的光。"""
    return mix(PANEL, TEXT, 0.26)


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


def _classic_stylesheet() -> str:
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
    /* 对话框不在 QMainWindow 体系里，不写这条就是系统默认的白底 ——
       深色主题下白底配深色按钮，像贴错了一张图 */
    QDialog {{
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


def app_stylesheet() -> str:
    """当前皮肤的全局样式表。"""
    if SKIN == "air":
        from . import theme_air

        return theme_air.app_stylesheet()
    return _classic_stylesheet()
