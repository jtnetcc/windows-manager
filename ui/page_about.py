"""关于软件：把「免费开源」摆到明面上，再交代版本与运行环境。

单开一页而不是塞进设置页末尾：用户拿到一个来路不明的清理工具，第一件事是
确认「收不收费、有没有后门」。挤在设置页最下面两行小字里，翻不到也不信。
这一页给图标、给承诺、给数据目录 —— 能自己点开看的东西才有说服力。

文案写死在模块里，不做成配置项：免费开源是软件的属性，不是能关掉的开关。
"""

from __future__ import annotations

import os
import platform
import time
from typing import Callable, NamedTuple

from PySide6 import __version__ as PYSIDE_VERSION
from PySide6.QtCore import Qt, Signal, qVersion
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core import applog, config, updater, version

from . import appicon, theme_dark as T
from .bg import BackgroundCall
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
    """一个下载来源。`url` 是**取值函数**，不是字符串。

    原因：国内直连那条要现签一个短时效地址（见 core/updater），签名十分钟
    就过期。写成模块级字符串的话，程序开着一小时后点「打开」就是死链。
    这里每次点击现取，`None` 表示这个渠道还没上架。

    `url()` 的返回值**永远不进界面**：不显示、不 hint、不进剪贴板。
    """

    name: str
    url: Callable[[], str] | None
    note: str


# GitHub 仓库地址。公开展示的是「项目在这」，不是下载直链 ——
# 界面上一律不出现，见下面 CHANNELS 的注释。
_GITHUB = "https://github.com/jtnetcc/windows-manager"


def _github() -> str:
    return _GITHUB


# 分发渠道。url 为 None 就显示「待接入」、不摆按钮 —— 再加一行就行，
# 页面代码不用动。
#
# **地址一律不上屏。** OSS 按外网流量计费，直链被截图、被转发一次，
# 后面别人拉的每一份都记在作者账上；桶现在是私有的，签名地址十分钟作废，
# 但界面上依然一个字都不摆 —— 用户要的是「点一下能下载」，不是把地址抄下来。
CHANNELS = [
    Channel(
        "GitHub",
        _github,
        "源码、版本说明与 zip 包都发布在这里",
    ),
    Channel(
        # 名字不叫「OSS 直链」：那是技术黑话，而且一听就知道是哪个存储桶。
        # 传函数本身：每次点击才取签名，拿到的才是新鲜的。
        "国内直连",
        updater.download_url,
        "免登录直接下载最新版，国内速度比 GitHub 快",
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

    def _restore_update_state(self) -> None:
        """页面会被重建（跟随系统主题延迟应用），重建后把上次结果接回去。"""
        from core import updater

        info = updater.last_result()
        if info is None:
            return
        if not info.ok:
            self.lbl_update.setText("检查失败")
            self.lbl_update.setStyleSheet(f"color: {T.TEXT_DIM};")
        elif info.has_update:
            self.lbl_update.setText(f"发现新版本 v{info.latest}")
            self.lbl_update.setStyleSheet(f"color: {T.AMBER};")
            self.btn_update.setText("去下载")
            # 重建后按钮还连着「检查更新」，不重接就会再检查一遍而不是下载
            try:
                self.btn_update.clicked.disconnect()
            except (RuntimeError, TypeError):
                applog.silenced("page_about._restore_update_state")
            self.btn_update.clicked.connect(
                lambda: self._open_channel(updater.download_url)
            )
        else:
            self.lbl_update.setText(f"已是最新（{version.text()}）")
            self.lbl_update.setStyleSheet(f"color: {T.GREEN};")

    def _check_update(self) -> None:
        """检查更新。只在点按钮时跑。

        **异步**：这一版之前是同步调用，冷启动/首次联网时 urllib 会在 DNS 上
        卡十几秒（timeout 管不到 getaddrinfo），界面跟着僵住。现在走
        BackgroundCall：线程里只调 updater.check()，主线程定时器来取结果，
        没有跨线程的信号槽生命周期问题。
        """
        if getattr(self, "_checking", False):
            return
        self._checking = True
        self.btn_update.setEnabled(False)
        self.lbl_update.setText("正在检查…")
        self.lbl_update.setStyleSheet(f"color: {T.TEXT_DIM};")

        if getattr(self, "_bg", None) is None:
            self._bg = BackgroundCall(self)

        def done(info, error: str) -> None:
            self._checking = False
            self.btn_update.setEnabled(True)

            if error or info is None:
                self.lbl_update.setText("检查失败")
                self.lbl_update.setToolTip(error or "")
                self.lbl_update.setStyleSheet(f"color: {T.TEXT_DIM};")
                return

            if not info.ok:
                self.lbl_update.setText("检查失败")
                self.lbl_update.setToolTip(info.error or "")
                self.lbl_update.setStyleSheet(f"color: {T.TEXT_DIM};")
                return

            if not info.has_update:
                self.lbl_update.setText(f"已是最新（{version.text()}）")
                self.lbl_update.setToolTip("")
                self.lbl_update.setStyleSheet(f"color: {T.GREEN};")
                return

            self.lbl_update.setText(f"发现新版本 v{info.latest}")
            self.lbl_update.setToolTip(info.notes or "")
            self.lbl_update.setStyleSheet(f"color: {T.AMBER};")
            self.btn_update.setText("去下载")
            try:
                self.btn_update.clicked.disconnect()
            except (RuntimeError, TypeError):
                applog.silenced("page_about._check_update")
            # 现取而不是用 info.url：签名十分钟过期，页面开着放一会儿再点，
            # 用检查时那一份就是死链。
            self.btn_update.clicked.connect(
                lambda: self._open_channel(updater.download_url)
            )

        if not self._bg.start(updater.check, done, timeout_ms=20000):
            # 后台还在跑（比如刚切页又点了一次），把状态复位，不要卡在「正在检查」
            self._checking = False
            self.btn_update.setEnabled(True)
            self.lbl_update.setText("正在检查…")

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
            "点「打开」在浏览器里下载；zip 包带 CRC 校验，传坏了解压时就能发现。",
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
        upd_row.setFixedHeight(26)
        upd = QHBoxLayout(upd_row)
        upd.setContentsMargins(0, 0, 0, 0)
        upd.setSpacing(10)

        # 左边叫「版本」而不是「检查更新」：否则和右边按钮的文字重复，
        # 一行里出现两次「检查更新」，看着像写错了
        ver_name = label("版本", 12, T.TEXT, QFont.Medium)
        ver_name.setFixedWidth(62)
        upd.addWidget(ver_name, 0)

        # 状态用文字，按钮只负责动作：按钮文字一变宽窄就跳，看着更乱
        self.lbl_update = label("尚未检查", 11, T.TEXT_DIM)
        upd.addWidget(self.lbl_update, 1)

        self.btn_update = QPushButton("检查更新")
        self.btn_update.setObjectName("Ghost")
        self.btn_update.setFixedSize(88, 24)
        self.btn_update.setCursor(Qt.PointingHandCursor)
        self.btn_update.clicked.connect(self._check_update)
        upd.addWidget(self.btn_update, 0)
        card.body().addWidget(upd_row)
        self._restore_update_state()
        return card

    def _channel_row(self, channel: Channel) -> QWidget:
        """一个来源：名称 + 一句说明 + 「打开」。"""
        box = QWidget()
        box.setFixedHeight(26)
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        name = label(channel.name, 12, T.TEXT, QFont.Medium)
        name.setFixedWidth(62)
        row.addWidget(name, 0)

        if channel.url is None:
            # 没上架就别摆一颗点不动的按钮，直接说清楚要等什么
            row.addWidget(label(f"待接入 · {channel.note}", 11, T.TEXT_DIM), 1)
            return box

        # 一行只有「名称 + 一句说明 + 打开」。**地址不建 label、不进 tooltip、
        # 不给「复制」** —— 复制等于把地址直接递进剪贴板，和显示一样是暴露。
        # 要下载就点按钮，由程序自己去那个地址。
        note = label(channel.note, 11, T.TEXT_DIM)
        row.addWidget(note, 1)

        # 用 Ghost 和「检查更新」保持一致。
        # 之前用 Tonal，它的 QSS 里带 min-height:30 和左右 16px 内边距，
        # 和 setFixedSize(56,24) 冲突，文字被挤得贴边，看着像坏了。
        btn_open = QPushButton("打开")
        btn_open.setObjectName("Ghost")
        btn_open.setFixedSize(56, 24)
        btn_open.setCursor(Qt.PointingHandCursor)
        # 传取值函数而不是字符串：签名十分钟过期，点的时候现取才新鲜。
        btn_open.clicked.connect(
            lambda _c=False, getter=channel.url: self._open_channel(getter)
        )
        row.addWidget(btn_open, 0)
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
        # 用户报问题时的唯一有效材料：环境摘要 + 日志尾部
        btn_diag = QPushButton("导出诊断信息")
        btn_diag.setObjectName("Ghost")
        btn_diag.setFixedHeight(28)
        btn_diag.setCursor(Qt.PointingHandCursor)
        btn_diag.clicked.connect(self._export_diagnostics)
        actions.addWidget(btn_copy)
        actions.addWidget(btn_dir)
        actions.addWidget(btn_diag)
        actions.addStretch(1)
        card.body().addLayout(actions)
        return card

    # ---------------------------------------------------------- 交互

    def _open_channel(self, getter: Callable[[], str]) -> None:
        """点「打开 / 去下载」：先取地址，再交给浏览器。

        取地址可能要走一次网络（换签名），所以先把提示画出来再请求，
        否则点下去那几百毫秒窗口是死的。`_opening` 防重入 ——
        重绘走的是 processEvents，这时候用户还能再点一次。
        """
        if getattr(self, "_opening", False):
            return
        self._opening = True
        try:
            self.message.emit("正在获取下载链接…")
            # 只为把上面那句话画出来；这一步会处理事件队列，所以上面的重入锁
            # 不是可选项
            QApplication.processEvents()
            try:
                url = getter()
            except Exception as exc:  # noqa: BLE001 — 界面线程里不许让异常穿出去
                self.message.emit(f"取下载链接失败：{exc}")
                return
            if not url:
                self.message.emit("这个渠道暂时不可用")
                return
            self._open_url(url)
        finally:
            self._opening = False

    def _open_url(self, url: str) -> None:
        # 打不开就直说打不开。**不要退化成「把地址复制到剪贴板」** ——
        # 那等于悄悄把地址递出去，用户一粘贴就上屏了。
        if not open_url(url):
            self.message.emit("打不开浏览器，请检查默认浏览器设置")
            return
        self.message.emit("已在浏览器中打开")

    def _copy_env(self) -> None:
        lines = [f"{APP_NAME} {version.text()}"]
        lines.append(f"界面框架：Qt {qVersion()} · PySide6 {PYSIDE_VERSION}")
        lines.append(f"运行环境：Python {platform.python_version()}")
        lines.append(
            f"操作系统：{platform.system()} {platform.release()}"
            f" · {platform.version()}"
        )
        lines.append(f"数据目录：{config.config_dir()}")
        # 这里原来会把各渠道的下载地址一并抄进剪贴板。删掉：用户要报问题时
        # 复制的是版本信息，不是分发链接，没必要让地址跟着跑出去。
        lines.append("免费开源 · 无广告 · 默认不联网")
        copy_text("\n".join(lines))
        self.message.emit("版本信息已复制")

    def _open_dir(self) -> None:
        try:
            os.startfile(str(config.config_dir()))  # noqa: S606
        except OSError:
            self.message.emit("打不开数据目录")

    def _export_diagnostics(self) -> None:
        """把环境摘要 + 日志尾部存成一个文件。

        用户说「数字不对」时，这一段比让他描述十句都管用。文件落在数据目录，
        路径同时复制到剪贴板，省得他截图给我看。
        """
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = applog.log_path().parent / f"diagnostics-{stamp}.txt"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(applog.diagnostics(version.text()), encoding="utf-8")
        except OSError as exc:
            self.message.emit(f"导出失败：{exc}")
            return
        copy_text(str(target))
        self.message.emit(f"诊断信息已保存：{target.name}（路径已复制）")
        try:
            os.startfile(str(target.parent))  # noqa: S606
        except OSError:
            pass
