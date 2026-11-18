"""主窗口用到的两个对话框。

从 `main_window.py` 里拆出来：那个文件一度有 1900 行，导航、托盘、标题栏、
任务路由全挤在一起，改一处要滚半天。这两个类自成闭环（不依赖 MainWindow），
拆走后主窗口只负责「装配 + 路由」。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core import applog, config, winmem

from . import theme_dark as T
from .components import label


class PowerModeDialog(QDialog):
    def __init__(self, fetch, parent=None):
        super().__init__(parent)
        self._fetch = fetch
        self.setWindowTitle("强力模式")
        self.setModal(True)
        self.resize(480, 420)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)

        root.addWidget(label("强力模式", 15, T.TEXT, QFont.Medium))
        root.addWidget(
            label(
                "以下进程占用较多内存且 CPU 空闲。结束它们会直接关闭对应程序，"
                "未保存的内容可能丢失。",
                10,
                T.TEXT_DIM,
            )
        )

        self.list = QListWidget()
        root.addWidget(self.list, 1)

        self.chk_active = QCheckBox("同时列出正在运行的程序（有风险）")
        self.chk_active.toggled.connect(self._reload)
        root.addWidget(self.chk_active)

        buttons = QDialogButtonBox()
        self.btn_ok = buttons.addButton("结束选中进程", QDialogButtonBox.AcceptRole)
        self.btn_ok.setObjectName("Primary")
        buttons.addButton("取消", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._hint = label("", 10, T.TEXT_DIM)
        root.addWidget(self._hint)
        self._reload()

    def _reload(self) -> None:
        self.list.clear()
        include_active = self.chk_active.isChecked()
        candidates = self._fetch(include_active)
        for cand in candidates:
            item = QListWidgetItem(
                f"{cand.name}    {winmem.fmt_size(cand.memory)}    "
                f"PID {cand.pid}    {cand.reason}"
            )
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            item.setData(Qt.UserRole, cand.pid)
            if cand.active:
                item.setForeground(QColor(T.AMBER))
            self.list.addItem(item)
        self.btn_ok.setEnabled(bool(candidates))
        if candidates:
            self._hint.setText(f"共 {len(candidates)} 个进程，默认全部不勾选")
        elif include_active:
            self._hint.setText("没有符合条件的进程")
        else:
            self._hint.setText("没有真正的闲置进程，可勾选上方选项查看全部")

    def selected_pids(self) -> list[int]:
        return [
            self.list.item(i).data(Qt.UserRole)
            for i in range(self.list.count())
            if self.list.item(i).checkState() == Qt.Checked
        ]


def apply_titlebar_theme(widget: QWidget) -> None:
    """标题栏跟随明暗：深色主题用 20/19 号属性，浅色主题显式设 0。

    对话框也要调：它们不是 QMainWindow，系统默认给白标题栏。
    """
    try:
        hwnd = int(widget.winId())
        dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        value = ctypes.c_int(1 if T.is_dark() else 0)
        for attr in (20, 19):
            if dwm.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                ctypes.c_int(attr),
                ctypes.byref(value),
                ctypes.sizeof(value),
            ) == 0:
                break
    except Exception:
        applog.silenced("main_window.apply_titlebar_theme")


class CloseChoiceDialog(QDialog):
    """点关闭按钮时问一句：留后台还是退出。

    「点叉等于关不掉」是最容易被当成程序卡死的设计，所以默认每次都问，
    勾上「记住」之后才退化成两态（写进 settings.close_action）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.choice: str | None = None
        self.remember = False
        self.setWindowTitle("关闭窗口")
        self.setModal(True)
        self.setFixedWidth(430)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)

        root.addWidget(label("关闭窗口", 15, T.TEXT, QFont.Medium))
        text = label(
            "留在右下角托盘可以继续监控，退出则不再接收告警。", 10, T.TEXT_DIM
        )
        text.setWordWrap(True)
        root.addWidget(text)
        root.addSpacing(2)

        self.chk_remember = QCheckBox("记住我的选择，下次不再询问")
        root.addWidget(self.chk_remember)
        root.addSpacing(2)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        # 缩到托盘是安全的一侧，做成主按钮、也吃掉回车
        self.btn_tray = QPushButton("最小化到托盘")
        self.btn_tray.setObjectName("Primary")
        self.btn_tray.setDefault(True)
        self.btn_tray.clicked.connect(lambda: self._pick(config.TRAY))
        # 退出放最右：挨着标题栏的叉，破坏性动作不往中间放
        btn_quit = QPushButton("退出程序")
        btn_quit.clicked.connect(lambda: self._pick(config.QUIT))
        buttons.addWidget(btn_cancel)
        buttons.addWidget(self.btn_tray)
        buttons.addWidget(btn_quit)
        root.addLayout(buttons)

    def _pick(self, action: str) -> None:
        self.choice = action
        self.remember = self.chk_remember.isChecked()
        self.accept()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_titlebar_theme(self)
