"""系统信息页：把本机配置一次列清楚。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core import sysinfo

from . import theme_dark as T
from .components import Card, label


class InfoRow(QWidget):
    """一行「标签 + 值」。值太长时靠 tooltip 兜底。"""

    def __init__(self, name: str, value: str = "", parent=None):
        super().__init__(parent)
        self.setFixedHeight(24)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.lbl_name = label(name, 12, T.TEXT_DIM)
        # 没有标签名（比如显卡列表）时不占列宽，值直接从左边开始
        self.lbl_name.setFixedWidth(76 if name else 0)
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


class SysInfoPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._info: sysinfo.SysInfo | None = None

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

        self.row_mem["总容量"].set_value(sysinfo.fmt_size(info.memory_total))
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
                    f"{sysinfo.fmt_size(module.size)}"
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
