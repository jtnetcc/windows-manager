"""主窗口：左侧分区导航 + 右侧内容区。"""

from __future__ import annotations

import ctypes
import os
import platform
import sys
import threading
import time
from ctypes import wintypes

from PySide6.QtCore import QEvent, QPoint, QRectF, QSignalBlocker, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from core import config, optimize, optimizer, single_instance, version, winmem
from core.alerts import LEVEL_CRIT, Alert, AlertConfig, AlertEngine, play_alert_sound
from core.metrics import MetricsSnapshot, fmt_bytes
from core.monitor import MetricsSampler, ProcessSampler
from core.store import Store
from core.task import TaskCenter

from . import appicon, icons, theme_dark as T, tooltip
from .components import NavButton, label

# 十四个页面模块不再在这里导入：启动只建首屏那一页，模块也跟着按需加载
# （见 _create_page）。全部导入光模块就要多占 9 MB 常驻内存，而其中大半
# 用户这一次根本不会打开。

# 侧栏导航按「看完 → 腾空间 → 改行为」三段排。
#
# 名称一律四个字：2/3/4 字混排时右边缘参差，看着像没对齐。压到两字做不到
# （「大文件」「回收站」「注册表」压成「文件」「回收」「注册」会失真），
# 所以统一往四字扩写，不改概念。key 是页面标识，改显示名不影响跳转，
# 页面内部文案也不用跟着改。
NAV_GROUPS = [
    (
        "概览",
        [
            ("health", "全面体检", "health"),
            ("monitor", "性能监控", "gauge"),
        ],
    ),
    (
        "清理",
        [
            ("clean", "垃圾清理", "clean"),
            ("appclean", "软件专清", "appclean"),
            ("dup", "重复文件", "duplicate"),
            ("bigfile", "超大文件", "file"),
            ("recycle", "回收中心", "recycle"),
        ],
    ),
    (
        "管理",
        [
            ("startup", "开机启动", "power"),
            ("tasks", "计划任务", "calendar"),
            ("slim", "系统瘦身", "slim"),
            ("software", "软件管理", "app"),
            ("registry", "注册表项", "tree"),
        ],
    ),
]

# 底部：低频的信息与设置。监控原来钉在这里，但它包含总览，是打开软件的第一屏，
# 压在十行之后等于藏起来 —— 已并入「概览」组。
# 「关于」放最后一项：它是全软件里唯一一页「不干活」的，位置固定在最下方
# 符合 Windows 软件的惯例，也顺手把 F1 接上。
NAV_FOOT = [
    ("sysinfo", "系统信息", "info"),
    ("settings", "偏好设置", "settings"),
    ("about", "关于软件", "about"),
]

# 首次进入某页才触发的加载动作：页面 key → 方法名。
#
# 之前这件事写在三个地方：__init__ 里十个 `_xxx_scanned` 布尔、一个
# LAZY_FLAGS 元组、switch_page 里九段 if。加一个页面要改三处，漏掉第三处
# 的表现是「换肤重建后那页空着」，只有走特定路径才复现。现在只有这一份。
LAZY_STARTERS = {
    "software": "refresh",
    "sysinfo": "refresh",
    "appclean": "start_scan",
    "slim": "start_scan",
    "health": "start_probe",
    "recycle": "start_scan",
    "tasks": "start_scan",
    "registry": "start_scan",
    "startup": "start_scan",
}

# 侧栏顺序就是导航顺序：标题、图标、快捷键全从这两张表派生。
# NAV_ITEMS 原来分两次拼接（中间还夹了两个函数定义），加项容易只加一半。
NAV_ITEMS = [(key, text) for _, items in NAV_GROUPS for key, text, _ in items]
NAV_ITEMS += [(key, text) for key, text, _ in NAV_FOOT]
PAGE_TITLES = dict(NAV_ITEMS)
PAGE_ICONS = {key: icon for _, items in NAV_GROUPS for key, _, icon in items}
PAGE_ICONS.update({key: icon for key, _, icon in NAV_FOOT})


def _current_user() -> str:
    """当前登录用户，只用来做侧栏提示，取不到就算了。"""
    return os.environ.get("USERNAME") or os.environ.get("USER") or ""


def _scan_stamp(scanned_at: float) -> str:
    """「（14:32 扫描）」；没有扫描时间就返回空串。"""
    if not scanned_at:
        return ""
    return time.strftime("（%H:%M 扫描）", time.localtime(scanned_at))

# 页面副标题，放在大标题下面一行
PAGE_SUBS = {
    "monitor": "实时监控 CPU、内存、磁盘与网络",
    "sysinfo": "本机硬件与系统参数一目了然",
    "health": "11 项指标加权评分与优化建议",
    "clean": "只清理可再生的临时文件与缓存",
    "appclean": "按软件清理缓存与已接收的图片视频",
    "slim": "关掉不需要的系统功能，收回能安全释放的空间",
    "dup": "按内容哈希找出重复副本",
    "bigfile": "按类型扫视频、安装包等大文件，勾选后送进回收站",
    "recycle": "回收站内容一览，按体积排序，可还原或清空",
    "startup": "开机自启的注册表项、快捷方式与计划任务",
    "tasks": "任务计划程序里的第三方任务，可启用或禁用",
    "registry": "卸载残留、启动项、App Paths 与 COM 组件的失效条目",
    "software": "已安装程序与卸载入口",
    "settings": "外观、监控、告警与启动",
    "about": "免费开源 · 版本与运行环境",
}

# 两个跨页入口的显示规则。两者重叠但不相同，原来各写一份枚举，
# 改一处忘另一处就会出现「点了可释放，跳过去却没有清理按钮」这种错位。
#
# RECLAIM_HIDDEN：不显示「可释放 X」。纯信息页没有可释放数据，软件专清与
#   系统瘦身自己有完整流程，右上角再挂一个跨页数字只会分散注意。
# NO_OPTIMIZE：连「一键优化 / 强力模式」也藏起来。「清理」组这几个页面
#   自己的主按钮才是这一屏唯一的实心蓝，再摆一个全局优化，用户不知道该点哪个。
RECLAIM_HIDDEN_PAGES = {
    "settings",
    "software",
    "sysinfo",
    "health",
    "appclean",
    "slim",
    "about",
}
NO_OPTIMIZE_PAGES = RECLAIM_HIDDEN_PAGES | {"clean", "dup", "bigfile", "startup"}

AUTO_COOLDOWN = 150.0


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


class MainWindow(QMainWindow):
    def __init__(self, settings: config.Settings):
        super().__init__()
        self.settings = settings
        self._snapshot: MetricsSnapshot | None = None
        self._optimizing = False
        self._optimize_auto = False
        self._last_auto = 0.0
        self._leaks: set[int] = set()
        self._last_proc_rows: list[dict] = []
        self._tray_hint_shown = False
        self._dark_titlebar_done = False
        # 已经触发过首次加载的页面 key，换肤重建时清空（见 _rebuild_for_theme）
        self._lazy_done: set[str] = set()
        # 托盘图标当前是否处于「内存高占用」状态，用来跳过重复重绘
        self._icon_danger = False
        # 数据仓库与任务中心：所有扫描结果统一走这里，页面只订阅
        self.store = Store(self)
        self.tasks = TaskCenter(self)
        self.tasks.finished.connect(self._on_task_finished)
        self.tasks.cancelled.connect(self._on_task_cancelled)
        self.tasks.failed.connect(self._on_task_failed)
        self.tasks.started.connect(self._on_task_started)
        self.tasks.progress.connect(self._on_task_progress)
        self.tasks.queue_changed.connect(self._refresh_task_label)
        self.alerts = AlertEngine(AlertConfig.from_settings(settings))

        self._nav: dict[str, NavButton] = {}
        self._pages: dict[str, QWidget] = {}
        # 默认落在全面体检：打开软件想知道的第一件事是「这台机器现在怎么样」。
        # 性能监控是一堆实时数字，看完还得自己判断，放在第二位更顺。
        self._current_page = "health"
        # 监控页停在哪一段。页面按需创建，换肤又会整页重建，
        # 记在这里才能在下次建出来时回到原来的分段。
        self._monitor_segment = "overview"

        self.setWindowTitle(f"Windows系统管家 {version.text()}")
        self.resize(1100, 720)
        self.setMinimumSize(980, 640)
        self.setWindowIcon(self._make_icon(0))

        self._build_toast()
        # 自绘提示框：Qt 自带的 QToolTip 在触发控件自己有样式表时会丢掉全局 QSS，
        # 深色系统 + 浅色主题下变成黑底黑字。接管 ToolTip 事件统一换成自己的。
        self._tip = tooltip.Tip()
        QApplication.instance().installEventFilter(self)
        self._build_ui()
        # 页面清理完不会走 store.apply()，而是直接 set_junk_items / set_dup_groups，
        # 所以顶部「可释放」必须自己订阅信号，否则清完了数字还挂着不动
        self.store.junk_changed.connect(self._refresh_status_bar)
        self.store.dup_changed.connect(self._refresh_status_bar)
        self._build_tray()
        self._start_samplers()
        # showEvent 里也会调一次，但窗口如果是隐藏启动（开机自启）就等不到，
        # 这里先设一遍，避免深色应用配浅色标题栏
        QTimer.singleShot(0, self._apply_titlebar)
        self._install_shortcuts()

    # ---------------------------------------------------------- 界面

    def _build_toast(self) -> None:
        """浮层提示只建一次，重建界面时复用，否则每次换肤都会多留一个。"""
        self._toast = QLabel(self)
        self._toast.setAlignment(Qt.AlignCenter)
        self._toast.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._toast.hide)

    # ---------------------------------------------------------- 提示框

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt 命名)
        """接管全应用的 ToolTip 事件，交给自绘提示框。

        不接管的话，Qt 会弹一个黑色的原生窗口：触发控件只要自己 setStyleSheet
        过（本项目的 QLabel 全都设了 color），全局 QSS 的 QToolTip 规则就不生效，
        底色回落到系统调色板。用户截图里就是纯黑底配深灰字，什么都读不出来。
        """
        kind = event.type()

        if kind == QEvent.ToolTip:
            if isinstance(obj, QWidget):
                text = tooltip.text_at(obj, event.pos())
                if text:
                    self._tip.show_text(text, event.globalPos())
                    return True
            return False

        if kind == QEvent.MouseMove:
            # 手抖几像素不收起，鼠标真的移开了才收
            if self._tip.isVisible() and self._tip.should_hide_for(
                event.globalPos()
            ):
                self._tip.hide()
        elif kind in (
            QEvent.Leave,
            QEvent.MouseButtonPress,
            QEvent.Wheel,
            QEvent.KeyPress,
            QEvent.WindowDeactivate,
        ):
            self._tip.hide()

        return super().eventFilter(obj, event)

    def _build_ui(self) -> None:
        self._nav.clear()
        self._pages.clear()
        old = self.centralWidget()

        root = QWidget()
        root.setObjectName("Root")
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._build_sidebar())
        row.addWidget(self._build_content(), 1)
        self.setCentralWidget(root)
        # 换肤重建时窗口已经显示，但新 central widget 不会自动接管可见性
        # （旧的那个被 setCentralWidget 摘掉后，新 widget 停在隐藏态），
        # 不显式 show 的话整个界面只剩一片底色。首次启动时窗口还没显示，
        # 这次 show 只是把标志位立起来，不会提前弹窗。
        root.show()

        # setCentralWidget 只是把旧的那一份 setParent(this) + hide()，**不会删**。
        # 换肤走的就是「重建整个界面」这条路，不手动收掉的话每换一次就留下
        # 一整套页面（实测 6 次后多出 8000 多个控件、120 个页面对象还在内存里）。
        if old is not None:
            old.deleteLater()

    def _build_sidebar(self) -> QWidget:
        side = QWidget()
        side.setObjectName("Sidebar")
        side.setFixedWidth(T.SIDEBAR_WIDTH)
        side.setStyleSheet(
            f"#Sidebar {{ background: {T.SIDEBAR};"
            f" border-right: 1px solid {T.BORDER}; }}"
        )
        layout = QVBoxLayout(side)
        layout.setContentsMargins(0, 8, 0, 10)
        # 导航项之间不留缝隙，否则点在 1px 间隙上会没反应
        layout.setSpacing(0)

        # 标题栏已经写着「Windows系统管家 · 版本」，侧栏再说一遍只是重复。
        # 这一行改成「这台机器」：主机名 + 登录用户，给顶部一点有用的上下文。
        host_row = QWidget()
        host_row.setFixedHeight(40)
        host_layout = QHBoxLayout(host_row)
        host_layout.setContentsMargins(18, 0, 16, 0)
        host_layout.setSpacing(7)

        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background: {T.ACCENT}; border-radius: 4px;")
        host_layout.addWidget(dot)

        host = (platform.node() or "").strip() or "本机"
        user = _current_user()
        host_label = label(host, 12, T.TEXT_DIM, QFont.Medium)
        # 主机名动辄 DESKTOP-8FJ2K9A，侧栏放不下，先按可用宽度省略
        host_label.setText(
            host_label.fontMetrics().elidedText(
                host, Qt.ElideRight, T.SIDEBAR_WIDTH - 62
            )
        )
        host_layout.addWidget(host_label, 1)
        host_row.setToolTip(f"{host}\n{user} · 当前用户" if user else host)
        layout.addWidget(host_row)
        layout.addSpacing(6)

        for index, (group, items) in enumerate(NAV_GROUPS):
            # 每组前面一个小标题。原来只画一条 1px 细线、不写字，等于把分组
            # 信息丢了 —— 十二个入口平铺下来，找东西全靠逐行读。
            if index:
                layout.addSpacing(4)
            layout.addWidget(self._nav_group_label(group))
            for key, text, icon in items:
                button = NavButton(key, text, self.switch_page, icon)
                layout.addWidget(button)
                self._nav[key] = button

        layout.addStretch(1)

        # 底部固定区：系统信息 + 偏好设置 + 权限徽标，和主分组用一条细线隔开
        layout.addWidget(self._nav_divider())
        for key, text, icon in NAV_FOOT:
            button = NavButton(key, text, self.switch_page, icon)
            layout.addWidget(button)
            self._nav[key] = button
        layout.addSpacing(6)

        admin_wrap = QWidget()
        admin_layout = QVBoxLayout(admin_wrap)
        admin_layout.setContentsMargins(10, 0, 10, 0)
        self.btn_admin = QPushButton()
        self.btn_admin.setObjectName("Ghost")
        self.btn_admin.setCursor(Qt.PointingHandCursor)
        self.btn_admin.clicked.connect(self._on_admin_clicked)
        admin_layout.addWidget(self.btn_admin)
        layout.addWidget(admin_wrap)
        self._refresh_admin_badge()

        return side

    @staticmethod
    def _nav_divider() -> QWidget:
        """导航底部分隔线。左右各留 14px，不顶到边。

        比分组标题再明显一档，让底部这块一眼看出是独立区域。
        """
        holder = QWidget()
        holder.setFixedHeight(19)
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(14, 10, 14, 8)
        layout.setSpacing(0)
        line = QLabel()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {T.BORDER_STRONG};")
        layout.addWidget(line)
        return holder

    @staticmethod
    def _nav_group_label(text: str) -> QWidget:
        """分组小标题。11px 弱化灰，和导航项的 13px 拉开一档。

        高度压到 22px：三组共 66px，靠行高 36→32 省下来的 40px 补回来，
        整块高度和改之前基本持平。
        """
        holder = QWidget()
        holder.setFixedHeight(22)
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(18, 0, 16, 0)
        layout.setSpacing(0)
        layout.addWidget(label(text, 11, T.TEXT_DIM))
        return holder

    def _build_content(self) -> QWidget:
        wrap = QWidget()
        wrap.setObjectName("PageArea")
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(24, 16, 24, 18)
        layout.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(0)
        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(1)
        self.lbl_title = label("概览", 26, T.TEXT, QFont.DemiBold)
        self.lbl_sub = label("", 12, T.TEXT_DIM)
        title_box.addWidget(self.lbl_title)
        title_box.addWidget(self.lbl_sub)
        top.addLayout(title_box)
        top.addStretch(1)

        self.lbl_task = label("", 12, T.ACCENT)
        top.addWidget(self.lbl_task, 0, Qt.AlignBottom)
        top.addSpacing(14)

        # 「可释放 X」做成按钮而不是纯文字：之前它只显示「尚未扫描」，
        # 用户不知道这是干嘛的，也不能点。
        self.btn_reclaim = QPushButton()
        self.btn_reclaim.setObjectName("Ghost")
        self.btn_reclaim.setFixedHeight(30)
        self.btn_reclaim.setCursor(Qt.PointingHandCursor)
        self.btn_reclaim.clicked.connect(self._on_reclaim_clicked)

        # 竖线把「现在能清多少」和「要做什么」分开：左边是状态，右边是动作。
        # 分隔线跟着按钮一起藏，否则没数据时右上角会挂一根孤零零的竖线。
        self._reclaim_box = QWidget()
        reclaim_row = QHBoxLayout(self._reclaim_box)
        reclaim_row.setContentsMargins(0, 0, 0, 0)
        reclaim_row.setSpacing(8)
        reclaim_row.addWidget(self.btn_reclaim)
        divider = QFrame()
        divider.setFixedSize(1, 15)
        divider.setStyleSheet(f"background: {T.BORDER_STRONG};")
        reclaim_row.addWidget(divider, 0, Qt.AlignVCenter)
        self._header_divider = divider
        self._reclaim_box.hide()
        top.addWidget(self._reclaim_box, 0, Qt.AlignBottom)

        self.btn_power = QPushButton("强力模式")
        self.btn_power.setObjectName("Ghost")
        self.btn_power.setFixedHeight(30)
        self.btn_power.setCursor(Qt.PointingHandCursor)
        self.btn_power.clicked.connect(self._open_power_mode)
        top.addWidget(self.btn_power, 0, Qt.AlignBottom)
        top.addSpacing(6)

        # 全局动作只给「柔和版」主按钮：页面自己的主按钮才是这一屏的唯一实心蓝，
        # 否则两行按钮各压一个蓝块，看起来像表格里的两列
        self.btn_optimize = QPushButton("一键优化")
        self.btn_optimize.setObjectName("Tonal")
        self.btn_optimize.setFixedHeight(30)
        self.btn_optimize.setCursor(Qt.PointingHandCursor)
        self.btn_optimize.clicked.connect(lambda: self.run_optimize())
        top.addWidget(self.btn_optimize, 0, Qt.AlignBottom)
        layout.addLayout(top)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        # 只建首屏那一页，其余等第一次切过去再建（见 _ensure_page）。
        # 以前十四页在启动时全部造好并塞进 QStackedWidget，控件数一千二百多，
        # 全局样式表还要逐个 polish —— 光这一项就多占二十多 MB，而用户一次
        # 只会看一页。
        self.switch_page(self._current_page)
        return wrap

    def __getattr__(self, name: str):
        """`window.page_health` 这类别名改成「要哪页建哪页」。

        页面按需创建之后，__init__ 里不能再挂一排 self.page_xxx 属性。
        这里按名字兜底，外部（测试脚本、调试）取页面的写法保持不变；
        只在常规属性查不到时才触发，不会遮蔽真实属性。
        """
        if name.startswith("page_"):
            key = name[5:]
            pages = self.__dict__.get("_pages")
            if pages is not None and key in PAGE_TITLES:
                return self._ensure_page(key)
        raise AttributeError(name)

    def _ensure_page(self, key: str) -> QWidget | None:
        """取页面，没建过就现建一个。"""
        page = self._pages.get(key)
        if page is not None:
            return page
        page = self._create_page(key)
        if page is None:
            return None
        self._pages[key] = page
        self.stack.addWidget(page)
        # 建好立刻喂一帧：不填的话切进来要先空一两个采样周期，
        # 看起来像「这页没数据」
        if self._snapshot is not None:
            feed = getattr(page, "update_metrics", None)
            if feed is not None:
                feed(self._snapshot)
        if self._last_proc_rows:
            feed_proc = getattr(page, "update_processes", None)
            if feed_proc is not None:
                total = self._snapshot.memory.total if self._snapshot else 0
                feed_proc(self._last_proc_rows, total, self._leaks)
        return page

    def _create_page(self, key: str) -> QWidget | None:
        """按 key 造页面。模块导入写在分支里，不打开的页连 import 都省了。"""
        if key == "monitor":
            from .pages_monitor import MonitorPage

            page = MonitorPage(self.store, self._navigate_action)
            page.kill_requested.connect(self._confirm_kill)
            page.kill_tree_requested.connect(self._confirm_kill_tree)
            page.whitelist_requested.connect(self._add_whitelist)
            page.segment_changed.connect(self._on_segment_changed)
            # 回到上次停的分段（换肤重建后尤其需要）
            if self._monitor_segment != "overview":
                page.set_segment(self._monitor_segment)
            return page

        if key == "health":
            from .page_health import HealthPage

            page = HealthPage(self.store, self.tasks)
            page.full_check_requested.connect(self.run_full_check)
            # 不达标项旁边的「处理」：直接翻到对应页，不叫用户自己去侧栏找
            page.navigate_requested.connect(self.switch_page)
            return page

        if key == "clean":
            from .page_clean import CleanPage

            return CleanPage(self.store, self.tasks)

        if key == "appclean":
            from .page_appclean import AppCleanPage

            return AppCleanPage(self.store, self.tasks)

        if key == "slim":
            from .page_slim import SlimPage

            page = SlimPage(self.tasks)
            page.message.connect(self.toast)
            # 提权的确认与重启走主窗口那套，页面不自己 ShellExecute
            page.elevate_requested.connect(self._on_admin_clicked)
            return page

        if key == "dup":
            from .page_dup import DupPage

            return DupPage(self.store, self.tasks)

        if key == "bigfile":
            from .page_bigfile import BigFilePage

            return BigFilePage(self.store, self.tasks)

        if key == "recycle":
            from .page_recycle import RecyclePage

            return RecyclePage(self.tasks)

        if key == "startup":
            from .page_startup import StartupPage

            return StartupPage(self.store, self.tasks)

        if key == "tasks":
            from .page_tasks import TasksPage

            return TasksPage(self.store, self.tasks)

        if key == "registry":
            from .page_registry import RegistryPage

            return RegistryPage(self.store, self.tasks)

        if key == "software":
            from .page_software import SoftwarePage

            return SoftwarePage(self.store, self.tasks)

        if key == "sysinfo":
            from .page_sysinfo import SysInfoPage

            return SysInfoPage()

        if key == "settings":
            from .page_settings import SettingsPage

            page = SettingsPage(self.settings)
            page.changed.connect(self._on_settings_changed)
            page.theme_changed.connect(self.apply_theme)
            page.accent_changed.connect(self.apply_accent)
            return page

        if key == "about":
            from .page_about import AboutPage

            page = AboutPage()
            page.message.connect(self.toast)
            return page

        return None

    def _set_memory_status(self, text: str, color: str) -> None:
        """把一行状态字送到「内存」分段。监控页没建就跳过 —— 为了一行字
        把整页造出来不值当，而且这时用户也看不到它。"""
        page = self._pages.get("monitor")
        if page is not None:
            page.set_memory_status(text, color)

    def switch_page(self, key: str) -> None:
        page = self._ensure_page(key)
        if page is None:
            # 静默返回会让「导航项和 _pages 对不上」这种错误藏很久：
            # 界面点着没反应，测试也看不出异常
            print(f"[nav] 页面不存在: {key}")
            return
        self._current_page = key
        self.stack.setCurrentWidget(page)
        self.lbl_title.setText(PAGE_TITLES.get(key, ""))
        # 监控页的副标题跟着内部分段走，其余页用固定文案
        sub = getattr(page, "subtitle", None)
        self.lbl_sub.setText(sub() if callable(sub) else PAGE_SUBS.get(key, ""))
        for name, button in self._nav.items():
            button.set_active(name == key)
        # 这些页没有可优化的东西，优化按钮别显示，
        # 否则用户会以为点一下能把系统信息也优化了
        show_opt = key not in NO_OPTIMIZE_PAGES
        self.btn_power.setVisible(show_opt)
        self.btn_optimize.setVisible(show_opt)
        # 分隔线是「状态 | 动作」之间的，动作没了就别留一根竖线挂在右上角
        self._header_divider.setVisible(show_opt)
        self._refresh_status_bar()
        if self._snapshot is not None and hasattr(page, "update_metrics"):
            page.update_metrics(self._snapshot)
        self._lazy_start(key)
        if key == "settings":
            self._refresh_cooldown_text()

    def _lazy_start(self, key: str) -> None:
        """首次进入才加载这一页：不拖慢启动，也不在后台抢磁盘。

        整份规则只看模块级 LAZY_STARTERS，页面对象由 self._pages 取，
        所以换肤重建后只要清空 _lazy_done 就能重新走一遍。
        """
        starter = LAZY_STARTERS.get(key)
        if starter is None or key in self._lazy_done:
            return
        self._lazy_done.add(key)
        page = self._pages[key]
        # 必须用带 context 的重载：换肤会换掉页面对象，裸 singleShot 会打到
        # 已经 deleteLater 的那一版上（清理页踩过，shiboken 直接崩）
        QTimer.singleShot(0, page, getattr(page, starter))

    def _on_segment_changed(self, key: str, subtitle: str) -> None:
        """监控页内部分段切换：只换副标题，侧栏选中和标题都不动。

        切到别的页面时分段器还在后台发信号（比如程序里同步状态），
        所以要再确认一次当前页，避免把别的页副标题改掉。
        """
        self._monitor_segment = key
        if self._current_page == "monitor":
            self.lbl_sub.setText(subtitle)

    # ---------------------------------------------------------- 体检数据

    def _navigate_action(self, key: str) -> None:
        """概览页待处理事项的点击入口。

        「做一次全面体检」不只是跳页，要顺手把体检派出去；
        其它项就是普通跳转。
        """
        if key == "__full_check__":
            self.switch_page("health")
            self.run_full_check()
            return
        self.switch_page(key)

    def run_full_check(self) -> None:
        """全面体检：把垃圾、查重、系统探测都派出去。

        任务中心串行执行，谁先完成谁先写进 store，体检页订阅了 store，
        所以每完成一项分数就往上走一格，不用等全部跑完。

        三页要先建出来：扫描范围（勾选的规则、查重的目录）是页面自己的状态，
        在这儿重写一份迟早和页面上的勾选对不上。这三页都很轻（各不到 1 MB），
        而且只有用户按下体检时才会发生。
        """
        for key in ("clean", "dup", "registry", "health"):
            self._ensure_page(key)
        self._pages["clean"].start_scan()
        self._pages["dup"].start_scan()
        self._pages["registry"].start_scan()
        self._pages["health"].start_probe()

    def _install_shortcuts(self) -> None:
        """Ctrl+1..9 按侧栏顺序切页，Ctrl+0 给第 10 项，F5 重扫，Ctrl+, 设置，F1 关于。

        侧栏有 15 个入口，数字键只够 10 个。底部这三项不占数字键 ——
        系统信息与关于都是纯查看页，偏好设置有 Ctrl+, 这个通用键位。
        """
        for index, (key, _text) in enumerate(NAV_ITEMS[:10], start=1):
            QShortcut(
                QKeySequence(f"Ctrl+{index % 10}"),
                self,
                activated=lambda k=key: self.switch_page(k),
            )
        QShortcut(QKeySequence("F5"), self, activated=self._rescan_current)
        QShortcut(
            QKeySequence("Ctrl+,"), self, activated=lambda: self.switch_page("settings")
        )
        # F1 是「这个软件是什么」的通用键位，和 Windows 帮助习惯一致
        QShortcut(QKeySequence("F1"), self, activated=lambda: self.switch_page("about"))

    def _rescan_current(self) -> None:
        """F5：重扫当前页面。没扫描能力的页面就什么都不做。"""
        page = self._pages.get(self._current_page)
        starter = getattr(page, "start_scan", None)
        if callable(starter):
            starter()

    def _on_task_started(self, task_id: str, _label: str) -> None:
        self._refresh_task_label()

    def _on_task_progress(self, task_id: str, _percent: int, _text: str) -> None:
        # 只有当前任务才更新文案，排队中的任务还没开始跑
        if task_id == self.tasks.current_id:
            self._refresh_task_label()

    def _refresh_task_label(self) -> None:
        """顶部常驻任务指示。

        之前主窗口一处都没订阅任务信号，切走页面后完全看不出后台在跑什么，
        用户会以为扫描没生效。
        """
        pending = self.tasks.pending()
        if not pending:
            self.lbl_task.setText("")
            return

        current_id, current_label = pending[0]
        text = f"{current_label}…"
        if len(pending) > 1:
            text += f"（+{len(pending) - 1} 排队）"
        self.lbl_task.setText(text)

    def _on_task_cancelled(self, task_id: str) -> None:
        if task_id != "optimize":
            return
        self._optimizing = False
        self.btn_optimize.setEnabled(True)
        self.btn_optimize.setText("一键优化")
        self._set_memory_status("已取消", T.TEXT_DIM)

    def _on_task_failed(self, task_id: str, label: str, error: str) -> None:
        """任务抛异常。页面只收到 cancelled、会自己复位，原因在这里统一说。

        不提示的话，用户看到的是「扫描到一半自己停了」，而且点几次都一样 ——
        之前异常和取消走同一条路，这种情况连日志都没有。一键优化的失败由
        它自己的完成回调处理（会给出「没有可释放的空间」），不重复弹。
        """
        if task_id == "optimize":
            return
        self.toast(f"{label or '操作'}失败：{error}", 6000, LEVEL_CRIT)

    def _on_task_finished(self, task_id: str, result) -> None:
        """任务中心的结果统一落到 store，页面各自订阅。"""
        if task_id == "recycle":
            # 回收中心没建就不必为了一份结果把它造出来：进去时会自己重扫
            page = self._pages.get("recycle")
            if page is not None:
                page.on_loaded(result or [])
            return
        if task_id == "optimize":
            self._on_optimized(result, getattr(self, "_optimize_auto", False))
            return
        if self.store.apply(task_id, result):
            self._refresh_status_bar()

    def _refresh_status_bar(self) -> None:
        """顶部「可释放 X」入口。没有数据就整个隐藏，不显示无意义的占位文案。

        数字是垃圾 + 重复文件的和，明细写在 tooltip 里，点开再选去哪一页。
        """
        parts = self.store.reclaimable_parts()
        total = sum(part[2] for part in parts)

        if self._current_page in RECLAIM_HIDDEN_PAGES:
            self._reclaim_box.hide()
            return
        if total <= 0:
            self._reclaim_box.hide()
            return

        self.btn_reclaim.setText(f"可释放 {fmt_bytes(total)} ▾")
        lines = [f"{name} {fmt_bytes(size)}{_scan_stamp(scanned_at)}"
                 for name, _page, size, scanned_at in parts]
        lines.append("点开可以选择清理哪一类")
        self.btn_reclaim.setToolTip("\n".join(lines))
        self._reclaim_box.show()

    def _on_reclaim_clicked(self) -> None:
        """「可释放」不再一律跳垃圾清理页 —— 4.9 GB 常常全是重复文件。"""
        parts = self.store.reclaimable_parts()
        if not parts:
            return
        if len(parts) == 1:
            self.switch_page(parts[0][1])
            return

        menu = QMenu(self)
        for name, page, size, scanned_at in parts:
            action = menu.addAction(
                f"{name}  {fmt_bytes(size)}{_scan_stamp(scanned_at)}"
            )
            action.triggered.connect(
                lambda _checked=False, target=page: self.switch_page(target)
            )
        menu.addSeparator()
        hint = menu.addAction("垃圾可直接清；重复文件要先确认保留哪一份")
        hint.setEnabled(False)
        menu.exec(self.btn_reclaim.mapToGlobal(QPoint(0, self.btn_reclaim.height() + 6)))

    def _refresh_cooldown_text(self) -> None:
        # 设置页还没建就先不写：它构造时会按当前告警状态自己填一遍
        page = self._pages.get("settings")
        if page is None:
            return
        if not self.alerts.config.enabled:
            page.set_cooldown_text("告警已关闭")
            return
        parts = [f"{name} {state}" for name, state in self.alerts.status_lines()]
        page.set_cooldown_text("冷却：" + " · ".join(parts))

    # ---------------------------------------------------------- 换肤

    def apply_theme(self, key: str) -> None:
        """切换主题模式（system / dark / light）。

        很多控件的颜色是构造时写进内联样式表的，改全局变量不会让它们变色，
        所以这里直接重建整个界面 —— 采样线程、托盘、设置都不受影响。

        注意「模式没变但配置要变」：用户从「浅色」切到「跟随系统」，而系统
        此刻正好也是浅色，set_theme 返回 False。这时也必须落盘，否则下次
        启动仍按旧的固定浅色走，跟随系统就等于没生效。
        """
        self.settings.theme = key
        self._apply_style(lambda: T.set_theme(key))

    def apply_accent(self, color: str) -> None:
        """切换强调色。空字符串表示跟随系统。同上，配置先落盘再决定要不要重建。"""
        self.settings.accent = color
        self._apply_style(lambda: T.set_accent(color or None))

    def _apply_style(self, setter) -> None:
        """主题和强调色只差一个 setter，落盘 / 重建 / 重绘色板这段共用。

        两条路原来各写一遍，改其中一条很容易漏掉另一条（比如忘记
        sync_theme_swatches 就会留一个和实际配色对不上的选中框）。
        """
        changed = setter()
        self.settings.save()
        if changed:
            self._rebuild_for_theme()
        else:
            # 设置页没建就不用同步色板：它构造时读的就是当前主题
            page = self._pages.get("settings")
            if page is not None:
                page.sync_theme_swatches()

    def _rebuild_for_theme(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(T.app_stylesheet())
        # 提示框是按主题色画的，换肤时它要是正挂在屏幕上，颜色会停在旧主题
        self._tip.hide()
        # 图标是按 (名字, 颜色) 缓存的，换肤后旧颜色再也用不到。不清的话
        # 用户每换一次强调色就多留一批渲染器，只有重启才会释放。
        icons.clear_cache()

        page_key = self._current_page
        self._stop_page_workers()
        # 重建会重新构造页面，懒加载记录必须清空，否则新页面以为
        # 「已经加载过」，进去一看全是横杠。之前只重置了其中三个，
        # 系统信息页换肤后就空掉了。
        self._lazy_done.clear()
        self._build_ui()
        self.switch_page(page_key)
        # 监控页停在哪一段记在 _monitor_segment 里，_create_page 会自己还原
        self._apply_titlebar()

        # 重建后新页面是空的，用最近一帧数据立刻填上，不然要等下一个采样周期
        if self._snapshot is not None:
            self.on_metrics(self._snapshot)
        if self._last_proc_rows:
            self.on_processes(self._last_proc_rows, self._leaks)

    # ---------------------------------------------------------- 采样

    def _start_samplers(self) -> None:
        self.metrics_sampler = MetricsSampler(self.settings.interval_ms, self)
        self.metrics_sampler.sampled.connect(self.on_metrics)
        self.metrics_sampler.start()

        self.proc_sampler = ProcessSampler(self.settings.process_interval_ms, self)
        self.proc_sampler.sampled.connect(self.on_processes)
        self.proc_sampler.start()

        # 跟随系统时不只在启动时读一次：用户在设置里切了深色模式，
        # 或按了「日落自动切换」，应用要跟着变，否则得重启才生效
        self._theme_watch = QTimer(self)
        self._theme_watch.setInterval(4000)
        self._theme_watch.timeout.connect(self._watch_system_theme)
        self._theme_watch.start()

    def _watch_system_theme(self) -> None:
        if self.settings.theme != "system":
            return
        # set_theme 内部会重读注册表，模式没变时返回 False，不会白重建界面
        if T.set_theme("system"):
            self._rebuild_for_theme()
            return
        # 强调色同理：只有用户没手选颜色（accent 为空）时才跟系统走
        if not self.settings.accent:
            if T.current_accent_base().upper() != T.system_accent().upper():
                if T.set_accent(None):
                    self._rebuild_for_theme()

    def on_metrics(self, snap: MetricsSnapshot) -> None:
        self._snapshot = snap
        for page in self._pages.values():
            if hasattr(page, "update_metrics"):
                page.update_metrics(snap)

        # 托盘提示要短：Windows 的通知区域会按固定宽度省略，
        # 之前带上「可用 6.4 GB」直接超宽，两行都被截成「…」
        self.tray.setToolTip(
            f"Windows系统管家\nCPU {snap.cpu.total:.0f}% · 内存 {snap.memory.percent:.0f}%"
        )
        # 图标上只有「是否高占用」这一位信息（右上角红点），每秒都重画一张
        # 64×64 图像纯属浪费。只有跨过 85% 这条线时才换图标。
        danger = snap.memory.percent >= 85
        if danger != self._icon_danger:
            self._icon_danger = danger
            self.tray.setIcon(self._make_icon(snap.memory.percent))
        self._check_auto_optimize(snap)
        self._process_alerts(snap)

    # ---------------------------------------------------------- 告警

    def _process_alerts(self, snap: MetricsSnapshot) -> None:
        for alert in self.alerts.feed(snap):
            self._show_alert(alert)

    def _show_alert(self, alert: Alert) -> None:
        if self.alerts.config.sound:
            play_alert_sound(alert.level)

        icon = (
            QSystemTrayIcon.MessageIcon.Critical
            if alert.level == LEVEL_CRIT
            else QSystemTrayIcon.MessageIcon.Warning
        )
        # 窗口在托盘里时靠气泡，窗口在前台时靠 toast，避免重复打扰
        if self.isVisible():
            self.toast(f"{alert.title} · {alert.message}", 5200, alert.level)
        elif self.tray.isVisible():
            self.tray.showMessage(alert.title, alert.message, icon, 6000)

    def on_processes(self, rows: list[dict], leaks: set[int]) -> None:
        self._leaks = leaks
        self._last_proc_rows = rows
        total = self._snapshot.memory.total if self._snapshot else 0
        for page in self._pages.values():
            handler = getattr(page, "update_processes", None)
            if handler is not None:
                handler(rows, total, leaks)

    # ---------------------------------------------------------- 优化

    def run_optimize(self, auto: bool = False) -> None:
        """一键优化：扫垃圾 → 清理 → 整理内存 → 汇总建议。

        以前这里只整理内存工作集，按钮叫「一键优化」名不副实。
        现在走真实链路，耗时操作交给任务中心，界面不卡。
        """
        if self.tasks.is_busy("optimize"):
            return
        self._optimizing = True
        self._optimize_auto = auto
        self.btn_optimize.setEnabled(False)
        self.btn_optimize.setText("优化中…")
        self._set_memory_status("正在优化…", T.TEXT_SUB)

        self.tasks.submit(
            "optimize",
            "一键优化",
            optimize.make_task(self.store, self.settings.deep_mode),
        )

    def _on_optimized(self, report, auto: bool) -> None:
        self._optimizing = False
        self.btn_optimize.setEnabled(True)
        self.btn_optimize.setText("一键优化")

        freed = report.freed if report else 0
        dup_left = self.store.dup.wasted if self.store.dup.ready else 0
        if freed > 0:
            self.settings.last_freed = freed
            self.settings.last_optimize_ts = time.time()
            self.settings.total_freed += freed
            self.settings.optimize_count += 1
            self.settings.save()
            self._set_memory_status(f"释放 {fmt_bytes(freed)} · 刚刚", T.GREEN)
            settings_page = self._pages.get("settings")
            if settings_page is not None:
                settings_page.refresh_stats()
            prefix = "自动优化" if auto else "优化完成"
            suffix = (
                f"，还有 {fmt_bytes(dup_left)} 重复文件待确认"
                if dup_left > 0 and not auto
                else ""
            )
            self.toast(f"{prefix}，释放 {fmt_bytes(freed)}{suffix}")
        else:
            # 一键优化只清垃圾和内存，重复文件要用户自己确认。这时候顶部还挂着
            # 「可释放 4.9 GB」，点「一键优化」得到的却是「没有可释放的空间」，
            # 看起来就像清不掉 —— 这里把话说清楚。
            if dup_left > 0 and not auto:
                self._set_memory_status(
                    f"重复文件 {fmt_bytes(dup_left)} 待确认", T.TEXT_DIM
                )
                self.toast(f"垃圾已清完，还有 {fmt_bytes(dup_left)} 重复文件待确认")
            else:
                self._set_memory_status("暂无可回收空间", T.TEXT_DIM)
                self.toast("没有可释放的空间" if not auto else "自动优化：无需处理")

        # 建议只提示，不自动执行 —— 重复文件和启动项需要用户自己确认
        if report and report.suggestions and not auto:
            self._show_suggestions(report)

    def _show_suggestions(self, report) -> None:
        lines = ["本次优化："]
        for step in report.steps:
            lines.append(f"  · {step.name}：{step.detail}")
        lines.append("")
        lines.append("还可以继续处理：")
        lines.extend(f"  · {s}" for s in report.suggestions)

        box = QMessageBox(self)
        box.setWindowTitle("优化完成")
        box.setText("\n".join(lines))
        box.setIcon(QMessageBox.Information)
        box.setStandardButtons(QMessageBox.Ok)
        # 建议里出现重复文件时给一个直达按钮：一键优化不替用户删这些文件，
        # 但「还得再找一遍入口」是没必要的摩擦
        go_dup = None
        if any("重复文件" in text for text in report.suggestions):
            go_dup = box.addButton("去看重复文件", QMessageBox.ActionRole)
        box.buttonClicked.connect(
            lambda button: self.switch_page("dup") if button is go_dup else None
        )
        # 用 show 不用 exec：exec 会在这里阻塞事件循环，
        # 任务中心排在后面的任务全被卡住
        box.setAttribute(Qt.WA_DeleteOnClose, True)
        box.show()
        self._suggestion_box = box

    def _check_auto_optimize(self, snap: MetricsSnapshot) -> None:
        if not self.settings.auto_optimize or self._optimizing:
            return
        if snap.memory.percent < self.settings.auto_threshold:
            return
        if time.time() - self._last_auto < AUTO_COOLDOWN:
            return
        self._last_auto = time.time()
        self.run_optimize(auto=True)

    def _open_power_mode(self) -> None:
        def fetch(include_active: bool):
            return optimizer.find_idle_candidates(
                set(self.settings.whitelist), include_active=include_active
            )

        dialog = PowerModeDialog(fetch, self)
        if dialog.exec() != QDialog.Accepted:
            return
        pids = dialog.selected_pids()
        if not pids:
            return
        self.proc_sampler.pause()
        ok, failed = optimizer.terminate_processes(pids)
        self.toast(f"已结束 {ok} 个进程" + (f"，{failed} 个失败" if failed else ""))
        QTimer.singleShot(800, self.proc_sampler.resume)

    def _confirm_kill(self, pid: int, name: str) -> None:
        answer = QMessageBox.question(
            self,
            "结束进程",
            f"确定要结束 {name}（PID {pid}）吗？\n未保存的数据可能丢失。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        ok, _ = optimizer.terminate_processes([pid])
        self.toast(f"已结束 {name}" if ok else "结束失败，可能需要更高权限")

    def _confirm_kill_tree(self, pid: int, name: str) -> None:
        answer = QMessageBox.question(
            self,
            "结束进程树",
            f"将结束 {name}（PID {pid}）以及它启动的所有子进程。\n"
            "浏览器、编辑器这类程序会连带关掉多个窗口，未保存的数据可能丢失。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.proc_sampler.pause()
        ok, failed = optimizer.terminate_tree(pid)
        self.toast(
            f"已结束 {ok} 个进程" + (f"，{failed} 个失败" if failed else "")
        )
        QTimer.singleShot(800, self.proc_sampler.resume)

    def _add_whitelist(self, name: str) -> None:
        if name.lower() not in {w.lower() for w in self.settings.whitelist}:
            self.settings.whitelist.append(name)
            self.settings.save()
            # 设置页没建也无所谓：它构造时会把名单整个读一遍
            page = self._pages.get("settings")
            if page is not None:
                page.wl_list.addItem(name)
            self.toast(f"{name} 已加入保护名单")

    def _on_settings_changed(self) -> None:
        self.metrics_sampler.set_interval(self.settings.interval_ms)
        self.alerts.configure(AlertConfig.from_settings(self.settings))
        self._refresh_cooldown_text()

    # ---------------------------------------------------------- 托盘

    def _build_tray(self) -> None:
        """托盘右键菜单。

        只放窗口关掉之后还用得上的几项：打开、优化、清理、自启、退出。
        参数类的设置留在设置页，托盘里翻二级菜单谁都不会去点。
        """
        self.tray = QSystemTrayIcon(self._make_icon(0), self)
        self.tray.setToolTip(f"Windows系统管家 {version.text()}")

        menu = QMenu(self)
        self._tray_menu = menu

        act_show = QAction("显示主窗口", self)
        act_show.triggered.connect(self.show_normal)
        act_opt = QAction("一键优化", self)
        act_opt.triggered.connect(lambda: self.run_optimize())
        act_clean = QAction("清理垃圾…", self)
        act_clean.triggered.connect(lambda: self._tray_goto("clean"))

        # 纯信息行：菜单打开时才知道当前占用，禁用态避免误点
        self._tray_state = QAction("正在采集…", self)
        self._tray_state.setEnabled(False)

        self._tray_startup = QAction("开机自启动", self)
        self._tray_startup.setCheckable(True)
        self._tray_startup.setChecked(config.is_autostart_enabled())
        self._tray_startup.toggled.connect(self._tray_toggle_startup)

        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self.quit_app)

        menu.addAction(act_show)
        menu.addAction(act_opt)
        menu.addAction(act_clean)
        menu.addSeparator()
        menu.addAction(self._tray_state)
        menu.addSeparator()
        menu.addAction(self._tray_startup)
        menu.addSeparator()
        menu.addAction(act_quit)
        # 默认项加粗：双击图标和点第一项是同一个动作
        menu.setDefaultAction(act_show)
        menu.aboutToShow.connect(self._sync_tray_menu)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _sync_tray_menu(self) -> None:
        """菜单弹出前刷新一次状态行与自启勾选，省得每秒都改菜单文本。"""
        parts = []
        if self._snapshot is not None:
            parts.append(f"内存 {self._snapshot.memory.percent:.0f}%")
        # 打开菜单才现算：扫描结果随时在变，缓存一份很容易和顶部按钮对不上
        reclaim = sum(part[2] for part in self.store.reclaimable_parts())
        if reclaim > 0:
            parts.append(f"可释放 {fmt_bytes(reclaim)}")
        self._tray_state.setText(" · ".join(parts) if parts else "正在采集…")

        # 注册表可能被安全软件或其它工具改过，以实际状态为准；
        # setChecked 会发 toggled，这里必须屏蔽，否则开个菜单就写注册表
        with QSignalBlocker(self._tray_startup):
            self._tray_startup.setChecked(config.is_autostart_enabled())

    def _tray_goto(self, page: str) -> None:
        """从托盘进某个页面：先把窗口叫回来，否则切了也看不见。"""
        self.show_normal()
        self.switch_page(page)

    def _tray_toggle_startup(self, checked: bool) -> None:
        if not config.set_autostart(checked):
            with QSignalBlocker(self._tray_startup):
                self._tray_startup.setChecked(not checked)
            self.toast("改不了开机自启，可能被安全软件拦了")
            return
        self.settings.autostart = checked
        self.settings.save()
        # 设置页的开关要跟着走，不然托盘开了、设置里还显示关着
        page = self._pages.get("settings")
        if page is not None:
            page.chk_startup.set_checked_silently(checked)
        self.toast("已开启开机自启" if checked else "已关闭开机自启")

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self.show_normal()

    def _make_icon(self, percent: float = 0.0) -> QIcon:
        """应用图标。内存高占用时右上角点一个红点，托盘里一眼能看出来。"""
        size = 64
        image = appicon.render(size)
        if percent >= 85:
            painter = QPainter(image)
            painter.setRenderHint(QPainter.Antialiasing, True)
            dot = size * 0.30
            painter.setPen(QPen(QColor("#FFFFFF"), max(2.0, size * 0.045)))
            painter.setBrush(QColor(T.RED))
            painter.drawEllipse(
                QRectF(size - dot - size * 0.03, size * 0.03, dot, dot)
            )
            painter.end()
        return QIcon(QPixmap.fromImage(image))

    # ---------------------------------------------------------- 权限

    def _refresh_admin_badge(self) -> None:
        """权限徽标：只在没有管理员权限时才出现。

        打包后的程序带 requireAdministrator，每次启动都是管理员，侧栏常驻一个
        「● 管理员权限」纯属占地方。只有从源码直接跑（普通权限）时才留这个提权入口。
        """
        is_admin = winmem.is_admin()
        self.btn_admin.setVisible(not is_admin)
        if is_admin:
            return
        self.btn_admin.setText("○ 普通权限 · 点击提权")
        self.btn_admin.setStyleSheet(
            f"QPushButton {{ background: transparent;"
            f" border: 1px solid {T.BORDER};"
            f" border-radius: 4px; color: {T.TEXT_DIM};"
            f" font-size: 11px; padding: 4px 6px; min-height: 24px; }}"
            f"QPushButton:hover {{ background: {T.HOVER}; color: {T.TEXT}; }}"
        )

    def _on_admin_clicked(self) -> None:
        if winmem.is_admin():
            self.toast("当前已是管理员权限")
            return
        answer = QMessageBox.question(
            self,
            "以管理员身份重启",
            "优化系统内存需要管理员权限。是否立即重启程序并提权？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            self._restart_as_admin()

    def _restart_as_admin(self) -> None:
        # 打包后 argv[0] 就是 exe 自己，传进去会变成「exe exe --elevated」；
        # 源码运行时 argv[0] 是脚本路径，必须带上才能重新跑起来。
        exe = sys.executable
        args = [
            a
            for a in (sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv)
            if a != "--elevated"
        ]
        args.append("--elevated")
        params = " ".join(f'"{a}"' for a in args)

        try:
            shell32 = ctypes.WinDLL("shell32", use_last_error=True)
            shell32.ShellExecuteW.argtypes = [
                wintypes.HWND,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                ctypes.c_int,
            ]
            shell32.ShellExecuteW.restype = ctypes.c_void_p
            result = shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        except Exception as exc:
            self.toast(f"提权失败：{exc}")
            return

        code = int(result or 0)
        if code <= 32:
            if code == 5:
                self.toast("已取消提权，继续以普通权限运行")
            else:
                self.toast(f"提权失败（{code}），请右键以管理员身份运行")
            return

        single_instance.release()
        self._shutdown()
        watchdog = threading.Timer(4.0, lambda: os._exit(0))
        watchdog.daemon = True
        watchdog.start()
        QApplication.quit()

    # ---------------------------------------------------------- 其他

    def toast(self, text: str, ms: int = 2600, level: str | None = None) -> None:
        border = T.BORDER_STRONG
        if level == LEVEL_CRIT:
            border = T.with_alpha(T.RED, 0.75)
        elif level is not None:
            border = T.with_alpha(T.AMBER, 0.75)
        self._toast.setStyleSheet(
            f"background: {T.TOAST_BG}; color: {T.TEXT}; border: 1px solid {border};"
            f" border-radius: 8px; padding: 8px 16px; font-size: 12px;"
        )
        self._toast.setText(text)
        self._toast.adjustSize()
        x = (self.width() - self._toast.width()) // 2
        y = self.height() - self._toast.height() - 24
        self._toast.move(max(16, x), max(16, y))
        self._toast.show()
        self._toast.raise_()
        self._toast_timer.start(ms)

    def show_normal(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._dark_titlebar_done:
            self._dark_titlebar_done = True
            self._apply_titlebar()

    def _apply_titlebar(self) -> None:
        """标题栏跟随明暗：深色主题用 20/19 号属性，浅色主题显式设 0。"""
        try:
            hwnd = int(self.winId())
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
            pass

    def closeEvent(self, event) -> None:
        if self.settings.minimize_to_tray and self.tray.isVisible():
            event.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.tray.showMessage(
                    "Windows系统管家",
                    "已最小化到托盘，双击图标可重新打开",
                    self.tray.MessageIcon.Information,
                    2500,
                )
            return
        self._shutdown()
        event.accept()

    def _stop_page_workers(self) -> None:
        """页面重建 / 退出前，先让扫描线程停下来。

        QThread 在运行中被析构会打印警告甚至崩溃，换肤时会频繁重建界面，
        必须先把线程收掉。
        """
        for page in list(self._pages.values()):
            handler = getattr(page, "shutdown", None)
            if handler is None:
                continue
            try:
                handler()
            except Exception:
                pass

    def _shutdown(self) -> None:
        self._stop_page_workers()
        # 页面只取消自己的任务，排队中的其它任务还挂在任务中心里。
        # 不在这里收尾的话，退出时 QThread 可能正在跑 —— 「QThread:
        # Destroyed while thread is still running」然后整个进程 abort。
        self.tasks.shutdown()
        for sampler in (
            getattr(self, "metrics_sampler", None),
            getattr(self, "proc_sampler", None),
        ):
            if sampler:
                sampler.stop()
        self.tray.hide()

    def quit_app(self) -> None:
        self._shutdown()
        QApplication.quit()
