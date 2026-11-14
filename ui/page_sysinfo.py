"""系统信息页：把本机配置一次列清楚。"""

from __future__ import annotations

import threading

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core import activation, diskinfo, sysinfo
from core.winmem import fmt_size

from . import theme_dark as T
from .components import Card, ElidedLabel, label
from core import applog


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
        layout.setContentsMargins(0, 5, 0, 5)
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

    def __init__(self, name: str, value: str = "", width: int = 76, parent=None):
        super().__init__(parent)
        self.setFixedHeight(24)
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


class SysInfoPage(QWidget):
    # 激活信息要起 cscript，一秒半起步，放主线程会把整页卡住。
    # 用普通线程 + 队列信号回传：QThread 在换肤重建时被析构会崩，
    # Python 线程不是 QObject，页面没了信号自然没人接，不用额外收尾。
    activation_ready = Signal(object)
    storage_ready = Signal(object)

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
        self.activation_ready.connect(self._on_activation)
        self.storage_ready.connect(self._on_storage)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll)

        body = QWidget()
        scroll.setWidget(body)
        grid = QGridLayout(body)
        grid.setContentsMargins(0, 0, 8, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)

        # -- 系统
        self.card_os = Card(padding=14, spacing=2)
        self.card_os.body().addWidget(
            self._title("系统", "info")
        )
        self.row_os = self._add_rows(
            self.card_os,
            ["版本", "版本号", "架构", "安装日期", "整机型号", "整机序列号",
             "计算机名", "当前用户"],
        )
        self.card_os.body().addStretch(1)
        grid.addWidget(self.card_os, 0, 0)

        # -- 处理器
        self.card_cpu = Card(padding=14, spacing=2)
        self.card_cpu.body().addWidget(self._title("处理器", "cpu"))
        self.row_cpu = self._add_rows(
            self.card_cpu, ["型号", "核心", "最高频率", "插槽"]
        )
        self.card_cpu.body().addStretch(1)
        grid.addWidget(self.card_cpu, 0, 1)

        # -- 内存
        self.card_mem = Card(padding=14, spacing=2)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(self._title("内存", "memory"))
        head.addStretch(1)
        self.lbl_slots = label("", 11, T.TEXT_DIM)
        head.addWidget(self.lbl_slots)
        self.card_mem.body().addLayout(head)
        self.row_mem = self._add_rows(self.card_mem, ["总容量"])
        self.mem_box = QVBoxLayout()
        self.mem_box.setSpacing(1)
        self.card_mem.body().addLayout(self.mem_box)
        self.card_mem.body().addStretch(1)
        grid.addWidget(self.card_mem, 1, 0)

        # -- 主板与固件
        self.card_board = Card(padding=14, spacing=2)
        self.card_board.body().addWidget(self._title("主板与固件", "disk"))
        self.row_board = self._add_rows(
            self.card_board, ["主板厂商", "主板型号", "主板序列号", "BIOS", "BIOS 日期"]
        )
        self.card_board.body().addStretch(1)
        grid.addWidget(self.card_board, 1, 1)

        # -- 存储设备：每块盘两行（型号+容量 / 序列号·固件·通电），后台填充
        self.card_disk = Card(padding=14, spacing=2)
        self.card_disk.body().addWidget(self._title("存储设备", "disk"))
        self.disk_box = QVBoxLayout()
        self.disk_box.setSpacing(6)
        self.card_disk.body().addLayout(self.disk_box)
        self.card_disk.body().addStretch(1)
        grid.addWidget(self.card_disk, 2, 0)

        # -- 显示与网络：显卡并进来（原来是独立一张卡，只有一行，
        # 却占掉整格高度，右下角大片空白）
        self.card_net = Card(padding=14, spacing=2)
        self.card_net.body().addWidget(self._title("显示与网络", "network"))
        self.gpu_box = QVBoxLayout()
        self.gpu_box.setSpacing(2)
        self.card_net.body().addLayout(self.gpu_box)
        self.row_net = self._add_rows(
            self.card_net, ["分辨率", "显示器", "IP 地址", "MAC 地址"]
        )
        self.card_net.body().addStretch(1)
        grid.addWidget(self.card_net, 2, 1)

        # -- 激活与授权：整行通铺，内部拆两列，六项刚好三行
        self.card_act = Card(padding=14, spacing=2)
        self.card_act.body().addWidget(self._title("激活与授权", "key"))
        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(28)
        self.row_act: dict[str, InfoRow] = {}
        for names in (
            ("激活状态", "激活方式", "系统激活时间"),
            ("硬件激活时间", "产品密钥", "产品 ID"),
        ):
            column = QVBoxLayout()
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(2)
            for name in names:
                # 默认 76 是给四个字留的，「系统激活时间」六个字会截断
                row = InfoRow(name, width=96)
                column.addWidget(row)
                self.row_act[name] = row
            columns.addLayout(column, 1)
        self.card_act.body().addLayout(columns)
        self.card_act.body().addStretch(1)
        grid.addWidget(self.card_act, 3, 0, 1, 2)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

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
    def _add_rows(card: Card, names: list[str]) -> dict[str, InfoRow]:
        rows: dict[str, InfoRow] = {}
        for name in names:
            row = InfoRow(name)
            card.body().addWidget(row)
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
            self.disk_box.addWidget(_DiskBlock(disk))

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
        row.set_value(act.status or ("未检测到" if not act.queried else "—"))
        row.set_color(colors.get(act.status, T.TEXT))
        row.lbl_value.setToolTip(
            act.status
            if act.queried
            else "读不到许可证状态（slmgr 不可用，或被安全策略拦截）"
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
        self.row_os["版本号"].set_value(
            f"{info.os_version}（{info.os_build}）" if info.os_version else info.os_build
        )
        self.row_os["架构"].set_value(info.architecture)
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
