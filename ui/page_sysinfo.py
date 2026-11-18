"""系统信息页：把本机配置一次列清楚。

布局围绕三件事，不是围绕「把格子填满」：

1. **顶部一条身份摘要**（系统 / 处理器 / 内存 / 激活）。不看卡片就知道这台
   机器是什么，不用先读八个标题再找。
2. **两列各自独立排布**（瀑布流）。原来用 `QGridLayout`，同一行的两张卡被
   强行拉成等高：「处理器」只有 4 行，却被撑到和「系统」8 行一样高，下面
   空一大片，看着像没加载完。现在每张卡只按自己的内容占高度。
3. **卡片 8 → 5 张**。系统与主板都是「这台机器是什么」，处理器与内存都是
   「算力」，各合成一张，卡内用小标题分段。
"""

from __future__ import annotations

import re
import threading

from PySide6.QtCore import QPointF, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core import activation, applog, diskinfo, health, sysinfo
from core.winmem import fmt_size

from . import theme_dark as T
from .components import Card, ElidedLabel, label


class _DiskBlock(QWidget):
    """一块物理盘：型号 / 规格 / 序列号，三行。

    原来是两行 —— 「型号 · 容量 · 介质」和「序列号 · 固件 · 通电」。值一长就被
    QLabel 硬裁，右边半个字都不剩，看着像没加载完。拆成三行之后每行都短，
    窄窗口也不会断；真放不下时还有省略号和完整提示框兜底。
    """

    # 「磁盘 0」实测 33px，"磁盘 10" 39px；40 是能放下又不浪费的最小值，
    # 序列号那行就靠省下来的这 20px 才不用画省略号
    TAG_W = 40

    def __init__(self, disk: diskinfo.Disk, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        # 和下面两行的缩进保持一致（TAG_W + 4），否则型号和副信息差半个字
        head.setSpacing(4)
        tag = label(f"磁盘 {disk.index}", 12, T.TEXT_DIM)
        tag.setFixedWidth(self.TAG_W)
        head.addWidget(tag)
        head.addWidget(
            ElidedLabel(disk.model or "未知型号", 12, T.TEXT, QFont.Medium), 1
        )
        layout.addLayout(head)

        # 规格：容量、介质、总线、固件、通电，按「先硬件后寿命」排。
        # 通电时间拿不到时（NVMe 系统不上报）整段省掉 —— 挂一个「通电 —」
        # 既占地方又像读取失败。
        spec = [fmt_size(disk.size)]
        if disk.media_text:
            spec.append(disk.media_text)
        if disk.bus:
            spec.append(disk.bus)
        if disk.firmware:
            spec.append(f"固件 {disk.firmware}")
        if disk.hours is not None:
            spec.append(f"通电 {disk.hours_text}")
        spec_row = ElidedLabel(" · ".join(spec), 11, T.TEXT_SUB)
        if disk.hours is None:
            spec_row.setToolTip(
                " · ".join(spec) + "\n这块盘的通电时间系统不上报（NVMe 常见），与权限无关"
            )
        layout.addLayout(self._indented(spec_row))

        # 序列号用等宽 10px：36 位串在窄窗口也能整行放下，不用省略号
        serial = ElidedLabel(
            f"SN {disk.serial or '—'}", 10, T.TEXT_DIM, use_mono=True
        )
        layout.addLayout(self._indented(serial))

    def _indented(self, widget: QWidget) -> QHBoxLayout:
        """左侧空出标签列宽度，让副信息对齐型号。

        间距取 4（而不是 10）：36 位序列号在 980 宽的最小窗口里只差这么点，
        缩进多给 6px 就得画省略号。
        """
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addSpacing(self.TAG_W + 4)
        row.addWidget(widget, 1)
        return row


class InfoRow(QWidget):
    """一行「标签 + 值」。值太长时靠 tooltip 兜底。"""

    # 23 而不是 24：整页 18 行，一行省 1px 就是 18px —— 正好是一屏放得下
    # 与「硬件健康」被切掉一半之间的差
    HEIGHT = 23

    def __init__(self, name: str, value: str = "", width: int = 76, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.lbl_name = label(name, 12, T.TEXT_DIM)
        # 没有标签名（比如显卡列表）时不占列宽，值直接从左边开始
        self.lbl_name.setFixedWidth(width if name else 0)
        self.lbl_name.setVisible(bool(name))
        # 值里会出现 40 字符的 NVMe 序列号、整行 BIOS 型号这类没有空格的长串。
        # 用 ElidedLabel 而不是 QLabel：QLabel 放不下就直接裁掉，右边半个字都
        # 不剩，看着像没加载完；而且它的 minimumSizeHint 按整段文本算，
        # 一个长串就能把整列顶宽，小窗口下右边那列被挤出可视区。
        self.lbl_value = ElidedLabel(value or "—", 12, T.TEXT)

        layout.addWidget(self.lbl_name, 0)
        layout.addWidget(self.lbl_value, 1)

    def set_value(self, value: str) -> None:
        text = value or "—"
        self.lbl_value.setText(text)
        self.lbl_value.setToolTip(text if value else "")

    def set_color(self, color: str) -> None:
        self.lbl_value.set_color(color)


class _IdentityItem(QWidget):
    """摘要带的一段：小标签在上，大值在下。"""

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addWidget(label(name, 11, T.TEXT_DIM))
        self.lbl_value = ElidedLabel("—", 14, T.TEXT, QFont.Medium)
        layout.addWidget(self.lbl_value)

    def set_value(self, text: str, color: str = "") -> None:
        value = text or "—"
        self.lbl_value.setText(value)
        self.lbl_value.setToolTip(value if text else "")
        self.lbl_value.set_color(color or T.TEXT)


class _IdentityBar(QWidget):
    """页面顶部的一行身份摘要。

    **不做成卡片**：卡片已经够多了，再加一张只会更碎。这里只画一条底部发丝
    线，视觉上是标题带的延伸，不是第七张卡。
    """

    NAMES = ("系统版本", "处理器", "内存", "激活状态")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(58)
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 0, 2, 12)
        row.setSpacing(18)
        self.items: dict[str, _IdentityItem] = {}
        for index, name in enumerate(self.NAMES):
            if index:
                row.addWidget(self._divider())
            item = _IdentityItem(name)
            row.addWidget(item, 1)
            self.items[name] = item

    @staticmethod
    def _divider() -> QWidget:
        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setFixedHeight(24)
        # 用 BORDER_STRONG 而不是 panel_edge：后者只混了 18%，四段之间那道
        # 竖线在深色底上根本看不见，四段就像挤在一起的一段文字
        line.setStyleSheet(f"color: {T.BORDER_STRONG};")
        return line

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setPen(QPen(QColor(T.panel_edge()), 1))
        y = self.height() - 1.5
        painter.drawLine(QPointF(0, y), QPointF(self.width(), y))
        painter.end()


def _short_cpu(name: str) -> str:
    """`Intel(R) Core(TM) i3-9100T CPU @ 3.10GHz` → `i3-9100T`。

    摘要带一段只放得下一行，完整型号在下面处理器卡里。认不出型号就退回
    去掉商标后缀的原文，宁可长一点，也不编一个不存在的型号。
    """
    if not name:
        return ""
    hit = re.search(
        r"\b(i[3579]-\d{3,5}[A-Z]{0,2}|[WE]-\d{4,5}[A-Z]{0,2}"
        r"|Ryzen\s+\d\s+\d{3,4}[A-Z]{0,2}|Ultra\s+[579]\s+\d{3}[A-Z]{0,2})\b",
        name,
        re.I,
    )
    if hit:
        return hit.group(1)
    text = re.sub(r"\((?:R|TM)\)|@.*$|\bCPU\b|\bProcessor\b", "", name, flags=re.I)
    return re.sub(r"\s{2,}", " ", text).strip(" -") or name


class SysInfoPage(QWidget):
    # 激活信息要起 cscript，一秒半起步，放主线程会把整页卡住。
    # 用普通线程 + 队列信号回传：QThread 在换肤重建时被析构会崩，
    # Python 线程不是 QObject，页面没了信号自然没人接，不用额外收尾。
    activation_ready = Signal(object)
    storage_ready = Signal(object)
    hardware_ready = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._info: sysinfo.SysInfo | None = None
        self._started = False
        self._activation: activation.ActivationInfo | None = None
        self._activation_loading = False
        self._disks: list = []
        self._storage_loading = False
        self._storage_done = False
        self._storage_error = ""
        self._hw_factors: list = []
        self._hw_loading = False
        self._hw_done = False
        self.activation_ready.connect(self._on_activation)
        self.storage_ready.connect(self._on_storage)
        self.hardware_ready.connect(self._on_hardware)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll)

        body = QWidget()
        scroll.setWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(0, 0, 8, 0)
        root.setSpacing(8)

        # -- 身份摘要：一眼看到这台机器是什么
        self.bar = _IdentityBar()
        root.addWidget(self.bar)

        # 全部卡片**通铺满宽**，卡内再分栏。
        #
        # 之前是两列瀑布流：空白确实没了，但两列卡片高度各不相同，屏幕上
        # 一路是错开的横线，看着乱。通铺之后左边缘、右边缘全对齐，纵向像
        # 一张清单；内容靠卡内分栏压扁，高度反而更省。
        def pair() -> tuple[QVBoxLayout, QVBoxLayout, QHBoxLayout]:
            """卡内左右两栏（语义分组），返回两个栏和它们的外层。"""
            outer = QHBoxLayout()
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(32)
            first = QVBoxLayout()
            first.setContentsMargins(0, 0, 0, 0)
            first.setSpacing(1)
            second = QVBoxLayout()
            second.setContentsMargins(0, 0, 0, 0)
            second.setSpacing(1)
            outer.addLayout(first, 1)
            outer.addLayout(second, 1)
            return first, second, outer

        # -- 本机：系统 / 主板 左右两栏
        self.card_os = Card(padding=11, spacing=1)
        self.card_os.body().addWidget(self._title("本机", "info"))
        left, right, columns = pair()
        left.addWidget(self._group("系统"))
        # 「架构」并进了「版本号」那一行：AMD64 单独占一行太浪费，而它本来
        # 就是版本信息的一部分
        self.row_os = self._add_rows(
            left,
            ["版本", "版本号", "安装日期", "整机型号", "整机序列号",
             "计算机名", "当前用户"],
        )
        right.addWidget(self._group("主板与固件"))
        self.row_board = self._add_rows(
            right, ["主板厂商", "主板型号", "主板序列号", "BIOS", "BIOS 日期"]
        )
        right.addStretch(1)
        self.card_os.body().addLayout(columns)
        root.addWidget(self.card_os)

        # -- 处理器与内存：算力放一张，左右两栏
        self.card_compute = Card(padding=11, spacing=1)
        self.card_compute.body().addWidget(self._title("处理器与内存", "cpu"))
        left, right, columns = pair()
        left.addWidget(self._group("处理器"))
        self.row_cpu = self._add_rows(left, ["型号", "核心", "最高频率", "插槽"])
        left.addStretch(1)
        self.lbl_slots = label("", 11, T.TEXT_DIM)
        right.addWidget(self._group("内存", self.lbl_slots))
        self.row_mem = self._add_rows(right, ["总容量"])
        self.mem_box = QVBoxLayout()
        self.mem_box.setSpacing(1)
        right.addLayout(self.mem_box)
        right.addStretch(1)
        self.card_compute.body().addLayout(columns)
        root.addWidget(self.card_compute)

        # -- 存储设备：多块盘横着并排，一块盘一栏
        self.card_disk = Card(padding=11, spacing=1)
        self.card_disk.body().addWidget(self._title("存储设备", "disk"))
        self.disk_box = QHBoxLayout()
        self.disk_box.setContentsMargins(0, 0, 0, 0)
        self.disk_box.setSpacing(28)
        self.card_disk.body().addLayout(self.disk_box)
        self.card_disk.body().addStretch(1)
        root.addWidget(self.card_disk)

        # -- 显示与网络：通铺。试过和「激活与授权」并排，两张卡各只剩约
        # 400px、卡内再分两栏，值只剩 86px —— IP 被截成「192.168.1…」、
        # 产品密钥被截成「NF6HC-QH89W-F8WY…」。**排得下不等于放得下内容**，
        # 宁可整页多滚 60px，也不让用户去悬停猜一个序列号。
        self.card_net = Card(padding=11, spacing=1)
        self.card_net.body().addWidget(self._title("显示与网络", "network"))
        self.gpu_box = QVBoxLayout()
        self.gpu_box.setSpacing(1)
        self.card_net.body().addLayout(self.gpu_box)
        left, right, columns = pair()
        self.row_net = self._add_rows(left, ["分辨率", "显示器"])
        self.row_net.update(self._add_rows(right, ["IP 地址", "MAC 地址"]))
        self.card_net.body().addLayout(columns)
        self.card_net.body().addStretch(1)
        root.addWidget(self.card_net)

        # -- 激活与授权：通铺两栏，六项刚好三行
        self.card_act = Card(padding=11, spacing=1)
        self.card_act.body().addWidget(self._title("激活与授权", "key"))
        left, right, columns = pair()
        self.row_act: dict[str, InfoRow] = {}
        for column, names in (
            (left, ("激活状态", "激活方式", "系统激活时间")),
            (right, ("硬件激活时间", "产品密钥", "产品 ID")),
        ):
            for name in names:
                # 默认 76 是给四个字留的，「系统激活时间」六个字会截断
                row = InfoRow(name, width=96)
                column.addWidget(row)
                self.row_act[name] = row
            column.addStretch(1)
        self.card_act.body().addLayout(columns)
        root.addWidget(self.card_act)

        # -- 硬件健康：从体检的 11 项里拆出来的 3 项，通铺一行三栏。
        # 这几项改不了，只能知道 —— 混在体检总分里会让用户误以为「72 分」
        # 说明硬盘要坏了，其实只是垃圾多。
        self.card_hw = Card(padding=11, spacing=1)
        self.card_hw.body().addWidget(self._title("硬件健康", "health"))
        self.hw_box = QHBoxLayout()
        self.hw_box.setContentsMargins(0, 0, 0, 0)
        self.hw_box.setSpacing(24)
        self.card_hw.body().addLayout(self.hw_box)
        self.card_hw.body().addStretch(1)
        root.addWidget(self.card_hw)

    @staticmethod
    def _title(text: str, icon: str) -> QWidget:
        holder = QWidget()
        holder.setFixedHeight(24)
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 6)
        layout.setSpacing(7)
        from .components import Icon

        layout.addWidget(Icon(icon, 14, T.ACCENT))
        layout.addWidget(label(text, 14, T.TEXT, QFont.DemiBold))
        layout.addStretch(1)
        return holder

    @staticmethod
    def _group(text: str, right: QWidget | None = None) -> QWidget:
        """卡片内部的分组小标题，右上角可以挂一句补充（比如「2 / 2 槽在用」）。"""
        holder = QWidget()
        holder.setFixedHeight(24)
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(label(text, 11, T.TEXT_DIM))
        layout.addStretch(1)
        if right is not None:
            layout.addWidget(right)
        return holder

    @staticmethod
    def _add_rows(
        layout: QVBoxLayout, names: list[str], width: int = 76
    ) -> dict[str, InfoRow]:
        """往一个栏里按顺序补 InfoRow。传的是栏（QVBoxLayout）不是卡片 ——
        卡片内部现在分左右两栏，行要分别落到各自的栏里。"""
        rows: dict[str, InfoRow] = {}
        for name in names:
            row = InfoRow(name, width=width)
            layout.addWidget(row)
            rows[name] = row
        return rows

    # ---------------------------------------------------------- 数据

    def showEvent(self, event) -> None:
        """第一次显示就把数据填上。

        不走主窗口的 LAZY_STARTERS：那条路是 `QTimer.singleShot(0, page, …)`，
        换肤重建后实测有约 1/3 概率没落地，页面停在全横杠状态（连「版本」
        都是空的）。showEvent 是 QStackedWidget 切页时同步发出的，不会丢。
        """
        super().showEvent(event)
        if not self._started:
            self._started = True
            self.refresh()

    def refresh(self) -> None:
        info = sysinfo.collect()
        self._info = info
        self._render(info)
        self._load_activation()
        self._load_storage()
        self._load_hardware()

    # ---------------------------------------------------------- 硬件健康

    def _load_hardware(self) -> None:
        """硬件三项要开物理磁盘句柄，慢，放后台。

        不依赖任何清理扫描 —— 系统信息页打开就该有数，哪怕用户从没跑过体检。
        """
        if self._hw_done:
            self._render_hardware(self._hw_factors)
            return
        if self._hw_loading:
            return
        self._hw_loading = True
        threading.Thread(target=self._fetch_hardware, daemon=True).start()

    def _fetch_hardware(self) -> None:
        try:
            snapshot = health.probe_system()
            factors = health.compute(snapshot).hardware_factors if snapshot else []
        except Exception:
            applog.failed("page_sysinfo.fetch_hardware")
            factors = []
        try:
            self.hardware_ready.emit(factors)
        except RuntimeError:
            applog.silenced("page_sysinfo._fetch_hardware")

    def _on_hardware(self, factors: list) -> None:
        self._hw_loading = False
        self._hw_done = True
        self._hw_factors = factors
        self._render_hardware(factors)

    def _render_hardware(self, factors: list) -> None:
        while self.hw_box.count():
            item = self.hw_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not factors:
            self.hw_box.addWidget(
                InfoRow("", "读取中…" if self._hw_loading else "—"), 1
            )
            return

        for factor in factors:
            row = InfoRow(factor.label, factor.detail or "—", width=96)
            ratio = factor.ratio
            if not factor.measured:
                row.set_color(T.TEXT_DIM)
            elif ratio >= 0.8:
                row.set_color(T.GREEN)
            elif ratio >= 0.5:
                row.set_color(T.AMBER)
            else:
                row.set_color(T.RED)
            if factor.score < factor.weight:
                row.lbl_value.setToolTip(
                    f"{factor.detail}\n得分 {factor.score:g}/{factor.weight}"
                )
            self.hw_box.addWidget(row, 1)

    # ---------------------------------------------------------- 存储

    def _load_storage(self) -> None:
        if self._storage_done:
            self._render_storage(self._disks)
            return
        if self._storage_loading:
            return
        self._storage_loading = True
        self._render_storage([])
        threading.Thread(target=self._fetch_storage, daemon=True).start()

    def _fetch_storage(self) -> None:
        try:
            disks = diskinfo.inventory()
        except Exception:
            applog.failed("page_sysinfo.fetch_storage")
            disks = []
        try:
            # 连失败原因一起带回去：否则页面只能说「未检测到物理磁盘」，
            # 而这台机器可能有三块盘，只是 PowerShell 没跑起来
            self.storage_ready.emit((disks, diskinfo.last_error()))
        except RuntimeError:
            applog.silenced("page_sysinfo._fetch_storage")

    def _on_storage(self, payload) -> None:
        disks, error = payload if isinstance(payload, tuple) else (payload, "")
        self._storage_loading = False
        self._storage_done = True
        self._disks = disks
        self._storage_error = error
        self._render_storage(disks)

    def _render_storage(self, disks: list) -> None:
        for index in reversed(range(self.disk_box.count())):
            widget = self.disk_box.itemAt(index).widget()
            if widget is not None:
                widget.deleteLater()

        if not disks:
            if self._storage_loading:
                hint = "读取中…"
            elif self._storage_error:
                hint = "读取失败"
            else:
                hint = "未检测到物理磁盘"
            row = InfoRow("", hint)
            if self._storage_error:
                row.set_color(T.AMBER)
                row.lbl_value.setToolTip(
                    f"{self._storage_error}\n\n"
                    "磁盘信息通过 PowerShell 读取，被安全软件拦截或策略限制时会失败。"
                    "完整原因见日志。"
                )
            self.disk_box.addWidget(row)
            return

        for disk in disks:
            # 一块盘一栏，横着并排：三块盘从「竖着占 9 行」变成「横着占 3 行」
            self.disk_box.addWidget(_DiskBlock(disk), 1)

    # ---------------------------------------------------------- 激活

    def _load_activation(self) -> None:
        """先占位再后台取，页面本身不等。"""
        if self._activation is not None:
            self._render_activation(self._activation)
            return
        if self._activation_loading:
            return
        self._activation_loading = True
        for row in self.row_act.values():
            row.set_value("读取中…")
        threading.Thread(target=self._fetch_activation, daemon=True).start()

    def _fetch_activation(self) -> None:
        try:
            result = activation.collect()
        except Exception:
            result = activation.ActivationInfo()
        try:
            self.activation_ready.emit(result)
        except RuntimeError:
            applog.silenced("page_sysinfo._fetch_activation")

    def _on_activation(self, result: activation.ActivationInfo) -> None:
        self._activation_loading = False
        self._activation = result
        self._render_activation(result)

    def _render_activation(self, act: activation.ActivationInfo) -> None:
        colors = {"已激活": T.GREEN, "未激活": T.RED, "宽限期": T.AMBER}

        row = self.row_act["激活状态"]
        status = act.status or ("未检测到" if not act.queried else "—")
        row.set_value(status)
        row.set_color(colors.get(act.status, T.TEXT))
        row.lbl_value.setToolTip(
            act.status
            if act.queried
            else "读不到许可证状态（slmgr 不可用，或被安全策略拦截）"
        )
        self.bar.items["激活状态"].set_value(
            status, colors.get(act.status, T.TEXT)
        )
        self.bar.items["激活状态"].lbl_value.setToolTip(
            " · ".join(x for x in (act.method, act.channel) if x) or status
        )

        self.row_act["激活方式"].set_value(act.method or "")
        self.row_act["激活方式"].lbl_value.setToolTip(act.method_note or "—")

        self.row_act["系统激活时间"].set_value(act.system_time)
        self.row_act["系统激活时间"].lbl_value.setToolTip(act.system_note or "—")

        self.row_act["硬件激活时间"].set_value(act.hardware_time)
        self.row_act["硬件激活时间"].lbl_value.setToolTip(act.hardware_note or "—")

        key_row = self.row_act["产品密钥"]
        key_row.set_value(act.display_key)
        if act.product_key:
            key_row.lbl_value.setToolTip("来自 BackupProductKeyDefault，可直接用于重装")
        elif act.partial_key:
            key_row.lbl_value.setToolTip(f"只读到末五位 {act.partial_key}")
        else:
            key_row.lbl_value.setToolTip("未读出产品密钥")

        self.row_act["产品 ID"].set_value(act.product_id)

    def _render(self, info: sysinfo.SysInfo) -> None:
        self.row_os["版本"].set_value(info.os_name)
        build = (
            f"{info.os_version}（{info.os_build}）" if info.os_version else info.os_build
        )
        if info.architecture:
            build = f"{build} · {info.architecture}" if build else info.architecture
        self.row_os["版本号"].set_value(build)
        self.row_os["安装日期"].set_value(info.install_date)
        self.row_os["整机型号"].set_value(info.system_model)
        self.row_os["整机序列号"].set_value(info.system_serial)
        self.row_os["计算机名"].set_value(info.hostname)
        self.row_os["当前用户"].set_value(info.username)

        self.row_cpu["型号"].set_value(info.cpu_name)
        cores = f"{info.cpu_cores} 核 / {info.cpu_threads} 线程" if info.cpu_cores else ""
        self.row_cpu["核心"].set_value(cores)
        self.row_cpu["最高频率"].set_value(
            f"{info.cpu_max_mhz} MHz" if info.cpu_max_mhz else ""
        )
        self.row_cpu["插槽"].set_value(info.cpu_socket)

        self.row_mem["总容量"].set_value(fmt_size(info.memory_total))
        self.lbl_slots.setText(
            f"{info.memory_used_slots} / {info.memory_slots} 槽在用"
            if info.memory_slots
            else ""
        )

        for index in reversed(range(self.mem_box.count())):
            widget = self.mem_box.itemAt(index).widget()
            if widget is not None:
                widget.deleteLater()
        if info.memory_modules:
            for module in info.memory_modules:
                line = InfoRow(
                    module.slot or "插槽",
                    f"{fmt_size(module.size)}"
                    + (f" · {module.speed} MT/s" if module.speed else "")
                    + (f" · {module.manufacturer}" if module.manufacturer else ""),
                )
                self.mem_box.addWidget(line)
        else:
            self.mem_box.addWidget(InfoRow("插槽", "固件未提供内存插槽信息"))

        self.row_board["主板厂商"].set_value(info.board_vendor)
        self.row_board["主板型号"].set_value(info.board_model)
        self.row_board["主板序列号"].set_value(info.board_serial)
        self.row_board["BIOS"].set_value(info.bios_version or info.bios_vendor)
        self.row_board["BIOS 日期"].set_value(info.bios_date)

        for index in reversed(range(self.gpu_box.count())):
            widget = self.gpu_box.itemAt(index).widget()
            if widget is not None:
                widget.deleteLater()
        if info.gpus:
            for name in info.gpus:
                self.gpu_box.addWidget(InfoRow("", name))
        else:
            self.gpu_box.addWidget(InfoRow("", "未检测到独立显卡"))

        self.row_net["分辨率"].set_value(info.screen)
        self.row_net["显示器"].set_value(f"{info.monitors} 台" if info.monitors else "")
        self.row_net["IP 地址"].set_value(info.ip)
        self.row_net["MAC 地址"].set_value(info.mac)
        # 「主机名」原来和上面「计算机名」是同一个值（都是 gethostname），删掉一处

        # -- 摘要带：这台机器是什么
        # 版本号只取短的一节（22H2 / 11），已经在系统名里的不重复拼——
        # `os_version` 有时是 "22H2"、有时是 "10.0.19045"，直接拼会变成
        # 「Windows 10 Pro 10」
        version = info.os_name or ""
        if info.os_version:
            short = info.os_version.split(".")[0]
            if short and short not in version:
                version = f"{version} {short}".strip()
        self.bar.items["系统版本"].set_value(version, T.TEXT)
        self.bar.items["系统版本"].lbl_value.setToolTip(
            f"{info.os_name} {info.os_version}（{info.os_build}）· {info.architecture}".strip()
        )

        cpu = _short_cpu(info.cpu_name)
        if cpu and info.cpu_cores:
            cpu = f"{cpu} · {info.cpu_cores} 核"
        self.bar.items["处理器"].set_value(cpu)
        self.bar.items["处理器"].lbl_value.setToolTip(info.cpu_name or "—")

        memory = fmt_size(info.memory_total) if info.memory_total else ""
        if memory and info.memory_slots:
            memory = f"{memory} · {info.memory_used_slots}/{info.memory_slots} 槽"
        self.bar.items["内存"].set_value(memory)
