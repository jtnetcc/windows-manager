"""关于软件：把「免费开源」摆到明面上，再交代版本与运行环境。

单开一页而不是塞进设置页末尾：用户拿到一个来路不明的清理工具，第一件事是
确认「收不收费、有没有后门」。挤在设置页最下面两行小字里，翻不到也不信。
这一页给图标、给承诺、给数据目录 —— 能自己点开看的东西才有说服力。

文案写死在模块里，不做成配置项：免费开源是软件的属性，不是能关掉的开关。
"""

from __future__ import annotations

import os
import platform
from typing import NamedTuple

from PySide6 import __version__ as PYSIDE_VERSION
from PySide6.QtCore import Qt, Signal, qVersion
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core import config, version

from . import appicon, theme_dark as T
from .components import Card, copy_text, font, label, open_url

APP_NAME = "Windows系统管家"
TAGLINE = (
    "一个只在本机运行的系统清理与监控工具：清垃圾、管启动项、看实时占用，"
    "不需要注册，也不需要联网。"
)

# 四条承诺。每条都能在代码里对上：没有付费判断、没有任何 http 请求、
# 没有静默安装逻辑、没有遥测上报。别往里加做不到的话。
PLEDGES = [
    ("永久免费", "全部功能开放，没有付费版、内购与功能锁"),
    ("开源可查", "源码完整提供，可以自己读、自己改、自己编译"),
    ("无广告无捆绑", "不弹广告、不改主页、不静默装别的软件"),
    ("默认不联网", "没有账号体系，不上传文件、不做统计；只有「检查更新」会主动联网一次"),
]

class Channel(NamedTuple):
    """一个下载来源。name 显示在左侧，url 为空视为「还没上架」。"""

    name: str
    url: str
    note: str


# 分发渠道。url 留空就显示「待接入」、按钮自动禁用 —— 后期上 OSS 只需要把
# 这里的 url 填上，或在列表里再加一行，页面代码不用动。
# 地址写在这里而不是从配置读：它是软件属性，用户改不了，也不该改。
CHANNELS = [
    Channel(
        "GitHub",
        "https://github.com/jtnetcc/windows-manager",
        "源码、版本说明与 zip 包都发布在这里",
    ),
]


def _badge(text: str, color: str) -> QLabel:
    """圆角小徽章。「免费 · 开源」靠它一眼可见。"""
    chip = QLabel(text)
    chip.setFont(font(10, QFont.Medium))
    chip.setStyleSheet(
        f"color: {color}; background: {T.with_alpha(color, 0.16)};"
        f" border: 1px solid {T.with_alpha(color, 0.38)};"
        f" border-radius: 9px; padding: 1px 9px;"
    )
    return chip


def _pledge(title: str, desc: str) -> QWidget:
    """一条承诺：勾 + 标题 + 一句说明。"""
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 3, 0, 3)
    row.setSpacing(8)

    mark = label("✓", 11, T.GREEN, QFont.Bold)
    mark.setFixedWidth(13)
    mark.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
    row.addWidget(mark, 0, Qt.AlignTop)

    text = QVBoxLayout()
    text.setSpacing(1)
    text.addWidget(label(title, 12, T.TEXT))
    detail = label(desc, 10, T.TEXT_DIM)
    detail.setWordWrap(True)
    text.addWidget(detail)
    row.addLayout(text, 1)
    return box


def _kv(name: str, value: str, tooltip: str = "") -> QWidget:
    """一行「名称 + 值」，值可选中复制。"""
    box = QWidget()
    box.setFixedHeight(22)
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(10)

    key = label(name, 12, T.TEXT_DIM)
    key.setFixedWidth(72)
    row.addWidget(key, 0)

    val = label(value or "—", 12, T.TEXT)
    val.setTextInteractionFlags(Qt.TextSelectableByMouse)
    if tooltip:
        val.setToolTip(tooltip)
    row.addWidget(val, 1)
    return box


def _elide(text: str, width: int) -> str:
    """按像素宽省略中间。路径这类长串用悬停看全貌，别在卡片里撑开一行。"""
    from PySide6.QtGui import QFontMetrics

    metrics = QFontMetrics(font(12))
    return metrics.elidedText(text, Qt.ElideMiddle, width)


class AboutPage(QWidget):
    message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll)

        body = QWidget()
        body.setMaximumWidth(760)
        scroll.setWidget(body)
        scroll.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        root = QVBoxLayout(body)
        root.setContentsMargins(0, 0, 6, 0)
        root.setSpacing(7)

        root.addWidget(self._hero())
        # 下载入口排在承诺之前：用户来这一页多半是找「去哪下、有没有新版」，
        # 先给地址，再解释免费开源。
        root.addWidget(self._channel_card())
        root.addWidget(self._promise_card())
        root.addWidget(self._env_card())

        root.addSpacing(2)
        # 收尾两句压到一行：这一页最好一屏看完，滚动条一动就看不到版权了
        root.addWidget(
            label(
                "清理与优化会改动文件和系统设置，动手前请看清确认框，重要数据自行备份。",
                10,
                T.TEXT_DIM,
            )
        )
        root.addWidget(
            label("© 2026 Windows系统管家 · 免费开源，源码公开", 10, T.TEXT_DIM)
        )
        root.addStretch(1)

    # ---------------------------------------------------------- 区块

    def _hero(self) -> QWidget:
        hero = QWidget()
        row = QHBoxLayout(hero)
        row.setContentsMargins(0, 2, 0, 6)
        row.setSpacing(14)

        icon = QLabel()
        # 按屏幕缩放渲染再交给 QLabel，150% 缩放下图标边缘才不会发糊
        ratio = self.devicePixelRatioF() or 1.0
        pixmap = QPixmap.fromImage(appicon.render(int(56 * ratio)))
        pixmap.setDevicePixelRatio(ratio)
        icon.setPixmap(pixmap)
        icon.setFixedSize(56, 56)
        row.addWidget(icon, 0, Qt.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(3)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.addWidget(label(APP_NAME, 20, T.TEXT, QFont.DemiBold), 0)
        title_row.addWidget(_badge("免费 · 开源", T.GREEN), 0, Qt.AlignVCenter)
        title_row.addStretch(1)
        col.addLayout(title_row)

        ver_row = QHBoxLayout()
        ver_row.setContentsMargins(0, 0, 0, 0)
        ver_row.setSpacing(8)
        ver_row.addWidget(label(f"{version.text()} · 本地运行 · 无需登录", 11, T.TEXT_DIM), 0)

        ver_row.addStretch(1)
        col.addLayout(ver_row)
        tagline = label(TAGLINE, 12, T.TEXT_SUB)
        tagline.setWordWrap(True)
        col.addWidget(tagline)

        row.addLayout(col, 1)
        return hero

    def _check_update(self) -> None:
        """检查更新。全应用唯一的联网动作，只在点按钮时跑。

        同步执行 + 先刷一次界面：请求通常 200ms 内返回，超时上限 4 秒。
        用线程回传会踩跨线程碰控件的坑，不值得为省几百毫秒冒险。
        """
        if getattr(self, "_checking", False):
            return
        self._checking = True
        self.btn_update.setEnabled(False)
        self.lbl_update.setText("正在检查…")
        self.lbl_update.setStyleSheet(f"color: {T.TEXT_DIM};")

        from PySide6.QtWidgets import QApplication

        QApplication.processEvents()

        from core import updater

        info = updater.check()
        self._checking = False
        self.btn_update.setEnabled(True)

        if not info.ok:
            reason = info.error or "未知原因"
            short = reason.split("：")[-1][:40]
            self.lbl_update.setText(f"检查失败：{short}")
            self.lbl_update.setToolTip(reason)
            self.lbl_update.setStyleSheet(f"color: {T.TEXT_DIM};")
            return

        if not info.has_update:
            self.lbl_update.setText("已是最新")
            self.lbl_update.setToolTip("")
            self.lbl_update.setStyleSheet(f"color: {T.GREEN};")
            return

        self.lbl_update.setText(f"发现 v{info.latest}")
        self.lbl_update.setToolTip(info.notes or "")
        self.lbl_update.setStyleSheet(f"color: {T.AMBER};")
        self.btn_update.setText("去下载")
        try:
            self.btn_update.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        self.btn_update.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(info.url))
        )

    def _promise_card(self) -> Card:
        card = Card(padding=12, spacing=9)
        card.body().addWidget(label("免费开源", 14, T.TEXT, QFont.DemiBold))
        card.body().addWidget(
            label("没有试用期，也没有藏起来的收费点；源码就在你手上。", 10, T.TEXT_DIM)
        )

        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(4)
        for index, (title, desc) in enumerate(PLEDGES):
            grid.addWidget(_pledge(title, desc), index // 2, index % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        card.body().addLayout(grid)
        return card

    def _channel_card(self) -> Card:
        card = Card(padding=12, spacing=4)
        card.body().addWidget(label("获取与分发", 14, T.TEXT, QFont.DemiBold))
        hint = label(
            "在浏览器里打开地址就能下载；zip 包带 CRC 校验，传坏了解压时就能发现。",
            10,
            T.TEXT_DIM,
        )
        hint.setWordWrap(True)
        card.body().addWidget(hint)
        card.body().addSpacing(2)
        for channel in CHANNELS:
            card.body().addWidget(self._channel_row(channel))

        # 检查更新放在分发卡里：它就是「怎么拿到新版」的一部分，
        # 界面只给一个按钮，不暴露具体地址。
        card.body().addSpacing(4)
        upd_row = QWidget()
        upd = QHBoxLayout(upd_row)
        upd.setContentsMargins(0, 0, 0, 0)
        upd.setSpacing(10)
        upd.addWidget(label("检查更新", 12, T.TEXT, QFont.Medium), 0)
        self.lbl_update = label("", 11, T.TEXT_DIM)
        upd.addWidget(self.lbl_update, 0)
        upd.addStretch(1)

        self.btn_update = QPushButton("检查更新")
        self.btn_update.setObjectName("Ghost")
        self.btn_update.setFixedHeight(24)
        self.btn_update.setCursor(Qt.PointingHandCursor)
        self.btn_update.clicked.connect(self._check_update)
        upd.addWidget(self.btn_update, 0)
        card.body().addWidget(upd_row)
        return card

    def _channel_row(self, channel: Channel) -> QWidget:
        """一个来源：名称 + 地址（未上架则显示状态）+ 复制 / 打开。"""
        box = QWidget()
        box.setFixedHeight(26)
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        name = label(channel.name, 12, T.TEXT, QFont.Medium)
        name.setFixedWidth(62)
        row.addWidget(name, 0)

        if not channel.url:
            # 没有地址就别摆一颗点不动的按钮，直接说清楚要等什么
            row.addWidget(label(f"待接入 · {channel.note}", 11, T.TEXT_DIM), 1)
            return box

        shown = channel.url.split("://", 1)[-1].rstrip("/")
        # 地址列给固定宽：两个来源的域名长短不一，跟着文字走会让右边的按钮
        # 一行一个位置。超出就省略中间，鼠标悬停看全貌。
        addr = label(_elide(shown, 240), 12, T.ACCENT)
        addr.setFixedWidth(240)
        addr.setTextInteractionFlags(Qt.TextSelectableByMouse)
        addr.setToolTip(f"{channel.url}\n{channel.note}")
        row.addWidget(addr, 0)

        btn_open = QPushButton("打开")
        btn_open.setObjectName("Tonal")
        btn_open.setFixedSize(56, 24)
        btn_open.setCursor(Qt.PointingHandCursor)
        btn_open.clicked.connect(lambda _c=False, url=channel.url: self._open_url(url))
        row.addWidget(btn_open, 0)

        btn_copy = QPushButton("复制")
        btn_copy.setObjectName("Ghost")
        btn_copy.setFixedSize(52, 24)
        btn_copy.setCursor(Qt.PointingHandCursor)
        btn_copy.clicked.connect(lambda _c=False, url=channel.url: self._copy_url(url))
        row.addWidget(btn_copy, 0)
        row.addStretch(1)
        return box

    def _env_card(self) -> Card:
        card = Card(padding=12, spacing=3)
        card.body().addWidget(label("运行环境", 14, T.TEXT, QFont.DemiBold))
        card.body().addSpacing(3)

        data_dir = str(config.config_dir())
        card.body().addWidget(_kv("版本", version.text()))
        card.body().addWidget(
            _kv("界面框架", f"Qt {qVersion()} · PySide6 {PYSIDE_VERSION}")
        )
        card.body().addWidget(_kv("运行环境", f"Python {platform.python_version()}"))
        card.body().addWidget(
            _kv(
                "操作系统",
                f"{platform.system()} {platform.release()}"
                f" · {platform.version()}",
            )
        )
        card.body().addWidget(
            _kv("数据目录", _elide(data_dir, 400), tooltip=data_dir)
        )
        card.body().addSpacing(6)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        btn_copy = QPushButton("复制版本信息")
        btn_copy.setObjectName("Ghost")
        btn_copy.setFixedHeight(28)
        btn_copy.setCursor(Qt.PointingHandCursor)
        btn_copy.clicked.connect(self._copy_env)
        btn_dir = QPushButton("打开数据目录")
        btn_dir.setObjectName("Ghost")
        btn_dir.setFixedHeight(28)
        btn_dir.setCursor(Qt.PointingHandCursor)
        btn_dir.clicked.connect(self._open_dir)
        actions.addWidget(btn_copy)
        actions.addWidget(btn_dir)
        actions.addStretch(1)
        card.body().addLayout(actions)
        return card

    # ---------------------------------------------------------- 交互

    def _open_url(self, url: str) -> None:
        if not open_url(url):
            # 没有默认浏览器的情况极少，但点了没反应比给个提示更糟
            copy_text(url)
            self.message.emit("打不开浏览器，地址已复制")
            return
        self.message.emit("已在浏览器中打开")

    def _copy_url(self, url: str) -> None:
        copy_text(url)
        self.message.emit("下载地址已复制")

    def _copy_env(self) -> None:
        lines = [f"{APP_NAME} {version.text()}"]
        lines.append(f"界面框架：Qt {qVersion()} · PySide6 {PYSIDE_VERSION}")
        lines.append(f"运行环境：Python {platform.python_version()}")
        lines.append(
            f"操作系统：{platform.system()} {platform.release()}"
            f" · {platform.version()}"
        )
        lines.append(f"数据目录：{config.config_dir()}")
        for channel in CHANNELS:
            if channel.url:
                lines.append(f"下载地址（{channel.name}）：{channel.url}")
        lines.append("免费开源 · 无广告 · 默认不联网")
        copy_text("\n".join(lines))
        self.message.emit("版本信息已复制")

    def _open_dir(self) -> None:
        try:
            os.startfile(str(config.config_dir()))  # noqa: S606
        except OSError:
            self.message.emit("打不开数据目录")
