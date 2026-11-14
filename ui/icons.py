"""线性图标。

用 SVG 路径描述、QSvgRenderer 渲染，好处是同一份定义可以用任意颜色和尺寸绘制，
不需要为每个状态准备图片。所有图标都是 16×16 视口、1.4 描边，视觉重量一致。
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF
from PySide6.QtGui import QPainter
from PySide6.QtSvg import QSvgRenderer

# 只放 path/rect/circle 这类图形，颜色和描边由外层统一给
PATHS: dict[str, str] = {
    "home": '<path d="M2.5 7.2 8 2.5l5.5 4.7V13a.8.8 0 0 1-.8.8H3.3a.8.8 0 0 1-.8-.8Z"/>',
    "cpu": (
        '<rect x="4.2" y="4.2" width="7.6" height="7.6" rx="1.2"/>'
        '<path d="M6.5 2v2.2M9.5 2v2.2M6.5 11.8V14M9.5 11.8V14'
        'M2 6.5h2.2M2 9.5h2.2M11.8 6.5H14M11.8 9.5H14"/>'
    ),
    "memory": (
        '<rect x="2" y="4.5" width="12" height="7" rx="1"/>'
        '<path d="M5 11.5V13M8 11.5V13M11 11.5V13M5.2 7h1.6M9.2 7h1.6"/>'
    ),
    "process": '<path d="M3 4.5h10M3 8h10M3 11.5h6.5"/>',
    "disk": '<rect x="2" y="3.5" width="12" height="9" rx="1.2"/><circle cx="8" cy="8" r="2.2"/>',
    "network": (
        '<path d="M2.2 6.4a8.2 8.2 0 0 1 11.6 0M4.4 8.8a5.2 5.2 0 0 1 7.2 0'
        'M6.6 11.2a2.2 2.2 0 0 1 2.8 0"/>'
    ),
    "health": (
        '<path d="M8 13.2S2.6 10 2.6 6.4a2.9 2.9 0 0 1 5.4-1.5 2.9 2.9 0 0 1 5.4 1.5'
        'c0 3.6-5.4 6.8-5.4 6.8Z"/>'
    ),
    "clean": (
        '<path d="M3.4 5.4h9.2M5.6 5.4V3.6h4.8v1.8"/>'
        '<path d="M4.6 5.4l.7 7.2a1 1 0 0 0 1 .8h3.4a1 1 0 0 0 1-.8l.7-7.2"/>'
    ),
    # 循环箭头：回收中心。扫帚在 16px 下怎么画都读成对勾（试过扇形、三根刷毛），
    # 所以反过来：垃圾桶留给最熟悉的「垃圾清理」，回收中心用循环箭头表示。
    "recycle": (
        '<path d="M13 8a5 5 0 1 1-1.6-3.7"/>'
        '<path d="M13.3 2.6v3h-3"/>'
        '<path d="M3 8a5 5 0 0 0 1.6 3.7"/>'
        '<path d="M2.7 13.4v-3h3"/>'
    ),
    # 仪表盘：性能监控的总览。指针要够长，短指针在 16px 下会散成
    # 「一段弧 + 一根斜线」，读不出是表盘。
    "gauge": (
        '<path d="M2.6 12a5.4 5.4 0 1 1 10.8 0"/>'
        '<path d="M8 12 10.8 7.4"/>'
        '<circle cx="8" cy="12" r="0.9"/>'
    ),
    # 日历：计划任务。原来用钟表，和「开机启动」的电源符号离得太近
    "calendar": (
        '<rect x="2.6" y="3.8" width="10.8" height="9.6" rx="1.4"/>'
        '<path d="M2.6 6.8h10.8M5.6 2.6v2.6M10.4 2.6v2.6"/>'
    ),
    # 树状结构：注册表。原来借了放大镜，那是「搜索」的意思
    "tree": (
        '<path d="M3.2 2.8v10.4h2.4"/>'
        '<path d="M3.2 8h2.4"/>'
        '<rect x="6.6" y="4.4" width="6.4" height="3.2" rx="0.9"/>'
        '<rect x="6.6" y="10" width="6.4" height="3.2" rx="0.9"/>'
    ),
    "duplicate": (
        '<rect x="2.4" y="2.4" width="7.2" height="7.2" rx="1.2"/>'
        '<rect x="6.4" y="6.4" width="7.2" height="7.2" rx="1.2"/>'
    ),
    "file": '<path d="M4 2.4h5l3 3v8.2H4Z"/><path d="M9 2.4v3h3"/>',
    "power": '<path d="M8 2.6v5"/><path d="M5 4.4a4.6 4.6 0 1 0 6 0"/>',
    "app": (
        '<path d="M8 2.2 13.4 5v6L8 13.8 2.6 11V5Z"/>'
        '<path d="M2.6 5 8 7.8 13.4 5M8 7.8v6"/>'
    ),
    # 软件专清：一个 App 方块 + 向下的箭头 = 「把这个应用里的东西清下去」。
    # 比「方块 + 垃圾桶角标」好在 16px 下角标不会糊成一团。
    "appclean": (
        '<rect x="2.6" y="2.6" width="10.8" height="10.8" rx="2.8"/>'
        '<path d="M8 5.2v3.4M6.5 7.1 8 8.6l1.5-1.5"/>'
        '<path d="M5.2 10.4h5.6"/>'
    ),
    # 系统瘦身：一个方框 + 向内收的双向箭头 = 「把系统压紧」。单画剪刀/扫帚
    # 在 16px 下都会糊成一团，双向箭头是唯一还能读出「收」的意思的。
    "slim": (
        '<rect x="2.6" y="3.4" width="10.8" height="9.2" rx="1.6"/>'
        '<path d="M5.2 8h5.6M6.6 6.5 5.1 8l1.5 1.5M9.4 6.5 10.9 8l-1.5 1.5"/>'
    ),
    "settings": (
        '<circle cx="8" cy="8" r="2.1"/>'
        '<path d="M8 2v1.6M8 12.4V14M2 8h1.6M12.4 8H14'
        'M3.8 3.8l1.1 1.1M11.1 11.1l1.1 1.1M12.2 3.8l-1.1 1.1M4.9 11.1l-1.1 1.1"/>'
    ),
    "bolt": '<path d="M8.8 2 4.6 8.8h3L7 14l4.4-7h-3.1Z"/>',
    "refresh": '<path d="M13 8a5 5 0 1 1-1.6-3.7"/><path d="M13.2 2.6v3h-3"/>',
    "folder": '<path d="M2 4.6h4.2l1.2 1.6h6.6v7H2Z"/>',
    "search": '<circle cx="7" cy="7" r="4.2"/><path d="M10.2 10.2 14 14"/>',
    "clock": '<circle cx="8" cy="8" r="5.8"/><path d="M8 4.8V8l2.3 1.5"/>',
    "info": (
        '<rect x="2" y="3" width="12" height="8.4" rx="1.2"/>'
        '<path d="M6 14h4M8 11.4V14"/>'
    ),
    # 「系统信息」用了屏幕图标，这里是圆圈加 i，两者才分得开
    "about": (
        '<circle cx="8" cy="8" r="5.7"/>'
        '<path d="M8 7.3v3.3"/><path d="M8 4.9v.2"/>'
    ),
    # 钥匙：激活与授权。齿做在杆的下侧，避免和「对勾」同形
    "key": (
        '<circle cx="5.4" cy="10.6" r="2.6"/>'
        '<path d="M7.4 8.8 13 3.2M9.6 6.6l1.3 1.3M11.2 5l1.2 1.2"/>'
    ),
}

_CACHE: dict[tuple[str, str], QSvgRenderer] = {}


def _renderer(name: str, color: str) -> QSvgRenderer | None:
    key = (name, color)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    body = PATHS.get(name)
    if body is None:
        return None
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" '
        f'stroke="{color}" stroke-width="1.5" stroke-linecap="round" '
        f'stroke-linejoin="round">{body}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        return None
    _CACHE[key] = renderer
    return renderer


def draw(painter: QPainter, name: str, rect: QRectF, color: str) -> bool:
    """在给定矩形里绘制图标，返回是否画成功。"""
    renderer = _renderer(name, color)
    if renderer is None:
        return False
    renderer.render(painter, rect)
    return True


def clear_cache() -> None:
    """换主题时清掉，避免颜色缓存无限增长。"""
    _CACHE.clear()
