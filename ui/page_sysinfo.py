"""系统信息页：把本机配置一次列清楚。"""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core import activation, sysinfo
from core.winmem import fmt_size

from . import theme_dark as T
from .components import Card, label


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
        self.lbl_value = label(value or "—", 12, T.TEXT)
        self.lbl_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        if value:
            self.lbl_value.setToolTip(value)

        layout.addWidget(self.lbl_name, 0)
        layout.addWidget(self.lbl_value, 1)

    def set_value(self, value: str) -> None:
        text = value or "—"
        self.lbl_value.setText(text)
        self.lbl_value.setToolTip(text if value else "")

    def set_color(self, color: str) -> None:
        self.lbl_value.setStyleSheet(
            f"color: {color}; background: transparent; border: none;"
        )


class SysInfoPage(QWidget):
    # 激活信息要起 cscript，一秒半起步，放主线程会把整页卡住。
    # 用普通线程 + 队列信号回传：QThread 在换肤重建时被析构会崩，
    # Python 线程不是 QObject，页面没了信号自然没人接，不用额外收尾。
    activation_ready = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._info: sysinfo.SysInfo | None = None
        self._activation: activation.ActivationInfo | None = None
        self._activation_loading = False
        self.activation_ready.connect(self._on_activation)

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
            self.card_os, ["版本", "版本号", "架构", "安装日期", "计算机名", "当前用户"]
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
            self.card_board, ["主板厂商", "主板型号", "BIOS", "BIOS 日期"]
        )
        self.card_board.body().addStretch(1)
        grid.addWidget(self.card_board, 1, 1)

        # -- 显卡
        self.card_gpu = Card(padding=14, spacing=2)
        self.card_gpu.body().addWidget(self._title("显卡", "app"))
        self.gpu_box = QVBoxLayout()
        self.gpu_box.setSpacing(2)
        self.card_gpu.body().addLayout(self.gpu_box)
        self.card_gpu.body().addStretch(1)
        grid.addWidget(self.card_gpu, 2, 0)

        # -- 显示与网络
        self.card_net = Card(padding=14, spacing=2)
        self.card_net.body().addWidget(self._title("显示与网络", "network"))
        self.row_net = self._add_rows(
            self.card_net, ["分辨率", "显示器", "IP 地址", "MAC 地址", "主机名"]
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

    def refresh(self) -> None:
        info = sysinfo.collect()
        self._info = info
        self._render(info)
        self._load_activation()

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
            pass  # 页面已经换肤销毁

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
        self.row_net["主机名"].set_value(info.hostname)
