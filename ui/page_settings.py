"""设置页（深色，内嵌在导航里而不是弹窗）。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core import config, winmem

from . import theme_dark as T
from .components import AccentDot, Card, ComboBox, ToggleSwitch, label

# 强调色预设。第一颗「默认」是品牌青色（value 为空串），最后那颗虚线环
# 才是「跟随 Windows」—— 顺序反了会让用户以为默认就跟系统走。
ACCENT_PRESETS = [
    "#0078D4",
    "#8764B8",
    "#CA5010",
    "#6D4FC4",
]


def _row(title: str, desc: str, control: QWidget) -> QWidget:
    wrap = QWidget()
    layout = QHBoxLayout(wrap)
    layout.setContentsMargins(0, 3, 0, 3)
    layout.setSpacing(16)

    text_box = QVBoxLayout()
    text_box.setSpacing(1)
    text_box.addWidget(label(title, 12, T.TEXT))
    if desc:
        text_box.addWidget(label(desc, 10, T.TEXT_DIM))
    layout.addLayout(text_box, 1)
    layout.addWidget(control, 0, Qt.AlignRight | Qt.AlignVCenter)
    return wrap


def _slider_row(
    title: str,
    desc: str,
    minimum: int,
    maximum: int,
    value: int,
    fmt,
    step: int = 1,
) -> tuple[QWidget, QSlider]:
    """滑块行。fmt(value) -> 右侧显示的文本。"""
    box = QWidget()
    layout = QHBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)

    slider = QSlider(Qt.Horizontal)
    slider.setFixedWidth(160)
    slider.setRange(minimum, maximum)
    slider.setSingleStep(step)
    slider.setPageStep(step)
    slider.setValue(value)

    caption = label(fmt(value), 11, T.TEXT_SUB)
    caption.setFixedWidth(58)
    caption.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    slider.valueChanged.connect(lambda v: caption.setText(fmt(v)))

    layout.addWidget(slider)
    layout.addWidget(caption)
    return _row(title, desc, box), slider


class SettingsPage(QWidget):
    changed = Signal()
    theme_changed = Signal(str)
    accent_changed = Signal(str)

    def __init__(self, settings: config.Settings, parent=None):
        super().__init__(parent)
        self.settings = settings

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll)

        body = QWidget()
        body.setMaximumWidth(820)
        scroll.setWidget(body)
        scroll.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        root = QVBoxLayout(body)
        root.setContentsMargins(0, 0, 6, 0)
        root.setSpacing(8)

        # -- 外观
        appearance = Card(padding=13, spacing=7)
        appearance.body().addWidget(label("外观", 14, T.TEXT, QFont.DemiBold))

        self.combo_mode = ComboBox()
        for key in T.MODES:
            # 默认档标出来：用户不用猜「我到底改没改过」
            suffix = "（默认）" if key == T.DEFAULT_THEME else ""
            self.combo_mode.addItem(T.MODE_NAMES[key] + suffix, key)
        # 宽度要放得下「跟随系统（默认）」这一档，窄了会把尾字裁掉
        self.combo_mode.setFixedWidth(172)
        current = T.current_mode()
        for index in range(self.combo_mode.count()):
            if self.combo_mode.itemData(index) == current:
                self.combo_mode.setCurrentIndex(index)
                break
        self.combo_mode.currentIndexChanged.connect(
            lambda _i: self.theme_changed.emit(self.combo_mode.currentData())
        )
        appearance.body().addWidget(
            _row("主题", "默认跟随系统，也可锁定浅色 / 深色", self.combo_mode)
        )

        accent_box = QWidget()
        accent_layout = QHBoxLayout(accent_box)
        accent_layout.setContentsMargins(0, 0, 0, 0)
        accent_layout.setSpacing(6)
        self._accent_dots: list[AccentDot] = []
        # 第一颗是「默认」品牌色，value 存空串
        default_dot = AccentDot(
            T.BRAND_ACCENT, selected=not settings.accent, value=""
        )
        default_dot.setToolTip("默认 · 冷青")
        default_dot.clicked.connect(self.accent_changed.emit)
        accent_layout.addWidget(default_dot)
        self._accent_dots.append(default_dot)

        pinned = (settings.accent or "").strip().upper()
        for color in ACCENT_PRESETS:
            dot = AccentDot(color, selected=bool(pinned) and color.upper() == pinned)
            dot.clicked.connect(self.accent_changed.emit)
            accent_layout.addWidget(dot)
            self._accent_dots.append(dot)

        # 最后一颗：跟随系统，虚线环标记，value 存 "system"
        sys_dot = AccentDot(
            T.system_accent(),
            selected=(settings.accent or "").strip().lower() == "system",
            value="system",
            system=True,
        )
        sys_dot.clicked.connect(self.accent_changed.emit)
        accent_layout.addWidget(sys_dot)
        self._accent_dots.append(sys_dot)

        accent_layout.addStretch(1)
        appearance.body().addWidget(
            _row("强调色", "默认冷青，也可跟随系统或自选", accent_box)
        )
        root.addWidget(appearance)

        # -- 监控
        monitor = Card(padding=13, spacing=3)
        monitor.body().addWidget(label("监控", 14, T.TEXT, QFont.DemiBold))
        monitor.body().addSpacing(4)

        self.slider_interval = QSlider(Qt.Horizontal)
        self.slider_interval.setFixedWidth(160)
        self.slider_interval.setRange(500, 3000)
        self.slider_interval.setSingleStep(250)
        self.slider_interval.setValue(settings.interval_ms)
        self.lbl_interval = label(f"{settings.interval_ms / 1000:.1f}s", 11, T.TEXT_SUB)
        self.lbl_interval.setFixedWidth(36)
        self.slider_interval.valueChanged.connect(self._on_interval)
        interval_box = QWidget()
        ib = QHBoxLayout(interval_box)
        ib.setContentsMargins(0, 0, 0, 0)
        ib.setSpacing(8)
        ib.addWidget(self.slider_interval)
        ib.addWidget(self.lbl_interval)
        monitor.body().addWidget(_row("刷新频率", "指标数据的更新间隔", interval_box))
        root.addWidget(monitor)

        # -- 优化
        optimize = Card(padding=13, spacing=3)
        optimize.body().addWidget(label("优化", 12, T.TEXT, QFont.Medium))
        optimize.body().addSpacing(4)

        self.chk_auto = ToggleSwitch()
        self.chk_auto.set_checked_silently(settings.auto_optimize)
        self.chk_auto.toggled.connect(self._on_auto)
        optimize.body().addWidget(
            _row("自动优化", "达到阈值自动清理一次，冷却 2.5 分钟", self.chk_auto)
        )

        self.slider_threshold = QSlider(Qt.Horizontal)
        self.slider_threshold.setFixedWidth(160)
        self.slider_threshold.setRange(60, 95)
        self.slider_threshold.setValue(settings.auto_threshold)
        self.slider_threshold.setEnabled(settings.auto_optimize)
        self.lbl_threshold = label(f"{settings.auto_threshold}%", 11, T.TEXT_SUB)
        self.lbl_threshold.setFixedWidth(36)
        self.slider_threshold.valueChanged.connect(self._on_threshold)
        threshold_box = QWidget()
        tb = QHBoxLayout(threshold_box)
        tb.setContentsMargins(0, 0, 0, 0)
        tb.setSpacing(8)
        tb.addWidget(self.slider_threshold)
        tb.addWidget(self.lbl_threshold)
        optimize.body().addWidget(
            _row("触发阈值", "使用率达到该值时自动优化", threshold_box)
        )

        self.chk_deep = ToggleSwitch()
        self.chk_deep.set_checked_silently(settings.deep_mode)
        self.chk_deep.toggled.connect(self._on_deep)
        optimize.body().addWidget(
            _row("深度优化", "释放更多，但耗时略长", self.chk_deep)
        )
        root.addWidget(optimize)

        # -- 告警
        alerts = Card(padding=13, spacing=3)
        alert_head = QHBoxLayout()
        alert_head.setContentsMargins(0, 0, 0, 0)
        alert_head.addWidget(label("告警", 14, T.TEXT, QFont.DemiBold))
        alert_head.addStretch(1)
        self.lbl_cooldown = label("", 10, T.TEXT_DIM)
        alert_head.addWidget(self.lbl_cooldown)
        alerts.body().addLayout(alert_head)
        alerts.body().addSpacing(4)

        self.chk_alerts = ToggleSwitch()
        self.chk_alerts.set_checked_silently(settings.alerts_enabled)
        self.chk_alerts.toggled.connect(self._on_alerts)
        alerts.body().addWidget(
            _row("启用告警", "指标异常时弹出通知", self.chk_alerts)
        )

        self.chk_sound = ToggleSwitch()
        self.chk_sound.set_checked_silently(settings.alert_sound)
        self.chk_sound.setEnabled(settings.alerts_enabled)
        self.chk_sound.toggled.connect(self._on_alert_sound)

        # 试听：合成音调绕开了系统声音方案，但用户没法预知响不响，
        # 给个按钮当场听一下最省事
        self.btn_sound_test = QPushButton("试听")
        self.btn_sound_test.setObjectName("Ghost")
        self.btn_sound_test.setFixedHeight(26)
        self.btn_sound_test.setCursor(Qt.PointingHandCursor)
        self.btn_sound_test.clicked.connect(self._test_sound)

        sound_row = QWidget()
        sound_layout = QHBoxLayout(sound_row)
        sound_layout.setContentsMargins(0, 3, 0, 3)
        sound_layout.setSpacing(12)
        sound_text = QVBoxLayout()
        sound_text.setSpacing(1)
        sound_text.addWidget(label("提示音", 12, T.TEXT))
        sound_text.addWidget(label("触发告警时播放提示音", 10, T.TEXT_DIM))
        sound_layout.addLayout(sound_text, 1)
        sound_layout.addWidget(self.btn_sound_test, 0, Qt.AlignRight | Qt.AlignVCenter)
        sound_layout.addWidget(self.chk_sound, 0, Qt.AlignRight | Qt.AlignVCenter)
        alerts.body().addWidget(sound_row)

        row, self.slider_alert_cpu = _slider_row(
            "CPU 阈值",
            "持续超过该占用率才告警，避免瞬时峰值误报",
            50,
            99,
            settings.alert_cpu,
            lambda v: f"{v}%",
            5,
        )
        self.slider_alert_cpu.valueChanged.connect(self._on_alert_cpu)
        alerts.body().addWidget(row)

        row, self.slider_alert_cpu_sec = _slider_row(
            "持续时间",
            "CPU 需要连续高于阈值多久",
            10,
            300,
            settings.alert_cpu_seconds,
            lambda v: f"{v}s" if v < 60 else f"{v // 60} 分",
            10,
        )
        self.slider_alert_cpu_sec.valueChanged.connect(self._on_alert_cpu_sec)
        alerts.body().addWidget(row)

        row, self.slider_alert_mem = _slider_row(
            "内存阈值",
            "内存占用率达到该值时告警",
            60,
            99,
            settings.alert_memory,
            lambda v: f"{v}%",
            5,
        )
        self.slider_alert_mem.valueChanged.connect(self._on_alert_mem)
        alerts.body().addWidget(row)

        row, self.slider_alert_disk = _slider_row(
            "磁盘剩余",
            "系统盘剩余空间低于该值时告警",
            1,
            50,
            int(settings.alert_disk_free_gb),
            lambda v: f"{v} GB",
            1,
        )
        self.slider_alert_disk.valueChanged.connect(self._on_alert_disk)
        alerts.body().addWidget(row)
        root.addWidget(alerts)

        # -- 行为
        behavior = Card(padding=13, spacing=3)
        behavior.body().addWidget(label("启动与窗口", 12, T.TEXT, QFont.Medium))
        behavior.body().addSpacing(4)

        self.chk_startup = ToggleSwitch()
        self.chk_startup.set_checked_silently(config.is_autostart_enabled())
        self.chk_startup.toggled.connect(self._on_startup)
        behavior.body().addWidget(
            _row("开机自启", "以普通权限静默启动到托盘", self.chk_startup)
        )

        # 点关闭按钮的三态。默认「每次询问」——旧版本是直接缩托盘，
        # 用户点叉没反应，第一反应是软件卡死了。
        self.combo_close = ComboBox()
        for key, name in (
            (config.ASK, "每次询问"),
            (config.TRAY, "最小化到托盘"),
            (config.QUIT, "直接退出"),
        ):
            self.combo_close.addItem(name, key)
        self.combo_close.setFixedWidth(144)
        index = self.combo_close.findData(settings.close_action)
        if index >= 0:
            self.combo_close.setCurrentIndex(index)
        self.combo_close.currentIndexChanged.connect(self._on_close_action)
        behavior.body().addWidget(
            _row("关闭窗口时", "缩到托盘可保留后台监控，直接退出则不再告警",
                 self.combo_close)
        )
        root.addWidget(behavior)

        # -- 保护名单
        whitelist = Card(padding=13, spacing=7)
        whitelist.body().addWidget(label("保护名单", 14, T.TEXT, QFont.DemiBold))
        whitelist.body().addWidget(
            label("名单内的进程不会被清理，也不会出现在强力模式里", 10, T.TEXT_DIM)
        )

        self.wl_list = QListWidget()
        self.wl_list.setFixedHeight(110)
        for name in settings.whitelist:
            QListWidgetItem(name, self.wl_list)
        whitelist.body().addWidget(self.wl_list)

        wl_actions = QHBoxLayout()
        wl_actions.setSpacing(8)
        btn_add = QPushButton("添加正在运行的程序…")
        btn_add.setObjectName("Ghost")
        btn_add.clicked.connect(self._add_whitelist)
        btn_remove = QPushButton("移除选中")
        btn_remove.setObjectName("Ghost")
        btn_remove.clicked.connect(self._remove_whitelist)
        wl_actions.addWidget(btn_add)
        wl_actions.addWidget(btn_remove)
        wl_actions.addStretch(1)
        whitelist.body().addLayout(wl_actions)
        root.addWidget(whitelist)

        # -- 统计
        stats = Card(padding=13, spacing=7)
        stats.body().addWidget(label("统计", 12, T.TEXT, QFont.Medium))
        self.lbl_stats = label("", 11, T.TEXT_SUB)
        stats.body().addWidget(self.lbl_stats)
        reset = QPushButton("重置统计")
        reset.setObjectName("Ghost")
        reset.clicked.connect(self._reset_stats)
        stats.body().addWidget(reset, 0, Qt.AlignLeft)
        root.addWidget(stats)

        # 版本号放最底部，一眼能看到当前跑的是哪一版；「免费开源」跟着露一句，
        # 免得有人以为这是要激活的试用版 —— 详细说明在「关于软件」页
        from core import version as app_version

        root.addWidget(
            label(
                f"Windows系统管家 {app_version.text()} · 免费开源",
                10,
                T.TEXT_DIM,
            )
        )

        root.addStretch(1)
        self.refresh_stats()

    # ---------------------------------------------------------- 交互

    def refresh_stats(self) -> None:
        s = self.settings
        self.lbl_stats.setText(
            f"累计优化 {s.optimize_count} 次 · 共释放 {winmem.fmt_size(s.total_freed)}"
        )

    def sync_theme_swatches(self) -> None:
        """主题被外部改掉时（比如跟随系统变了），把控件状态同步过来。"""
        mode = T.current_mode()
        for index in range(self.combo_mode.count()):
            if self.combo_mode.itemData(index) == mode:
                self.combo_mode.blockSignals(True)
                self.combo_mode.setCurrentIndex(index)
                self.combo_mode.blockSignals(False)
                break
        pinned = (self.settings.accent or "").strip()
        for dot in self._accent_dots:
            if dot.is_system():
                # 系统色的点要跟着系统当前色实时变，否则和实际配色对不上
                dot.set_color(T.system_accent())
                dot.set_selected(pinned.lower() == "system")
            elif dot.value() == "":
                dot.set_selected(not pinned)
            else:
                dot.set_selected(pinned.upper() == dot.value().upper())

    def _save(self) -> None:
        self.settings.save()
        self.changed.emit()

    def _on_interval(self, value: int) -> None:
        self.lbl_interval.setText(f"{value / 1000:.1f}s")
        self.settings.interval_ms = value
        self._save()

    def _on_auto(self, checked: bool) -> None:
        self.slider_threshold.setEnabled(checked)
        self.settings.auto_optimize = checked
        self._save()

    def _on_threshold(self, value: int) -> None:
        self.lbl_threshold.setText(f"{value}%")
        self.settings.auto_threshold = value
        self._save()

    def _on_deep(self, checked: bool) -> None:
        self.settings.deep_mode = checked
        self._save()

    def _on_startup(self, checked: bool) -> None:
        config.set_autostart(checked)
        self.settings.autostart = checked
        self._save()

    def _on_close_action(self, index: int) -> None:
        action = self.combo_close.itemData(index)
        if action in config.CLOSE_ACTIONS:
            self.settings.close_action = action
            self._save()

    def set_close_action(self, action: str) -> None:
        """主窗口在关闭对话框里勾了「记住」，这里跟着走。"""
        index = self.combo_close.findData(action)
        if index < 0 or index == self.combo_close.currentIndex():
            return
        self.combo_close.blockSignals(True)
        self.combo_close.setCurrentIndex(index)
        self.combo_close.blockSignals(False)

    # -- 告警 ------------------------------------------------------

    def _test_sound(self) -> None:
        from core import alerts

        alerts.play_alert_sound(alerts.LEVEL_WARN)

    def _on_alerts(self, checked: bool) -> None:
        self.settings.alerts_enabled = checked
        self.chk_sound.setEnabled(checked)
        for slider in (
            self.slider_alert_cpu,
            self.slider_alert_cpu_sec,
            self.slider_alert_mem,
            self.slider_alert_disk,
        ):
            slider.setEnabled(checked)
        self._save()

    def _on_alert_sound(self, checked: bool) -> None:
        self.settings.alert_sound = checked
        self._save()

    def _on_alert_cpu(self, value: int) -> None:
        self.settings.alert_cpu = value
        self._save()

    def _on_alert_cpu_sec(self, value: int) -> None:
        self.settings.alert_cpu_seconds = value
        self._save()

    def _on_alert_mem(self, value: int) -> None:
        self.settings.alert_memory = value
        self._save()

    def _on_alert_disk(self, value: int) -> None:
        self.settings.alert_disk_free_gb = float(value)
        self._save()

    def set_cooldown_text(self, text: str) -> None:
        self.lbl_cooldown.setText(text)

    def _add_whitelist(self) -> None:
        import psutil

        names = set()
        for proc in psutil.process_iter(["name"]):
            try:
                if proc.info["name"]:
                    names.add(proc.info["name"])
            except Exception:
                continue
        existing = {w.lower() for w in self.settings.whitelist}
        candidates = sorted(
            [n for n in names if n.lower() not in existing], key=str.lower
        )
        if not candidates:
            return
        name, ok = QInputDialog.getItem(
            self, "添加到保护名单", "选择进程：", candidates, 0, False
        )
        if ok and name:
            QListWidgetItem(name, self.wl_list)
            self.settings.whitelist.append(name)
            self._save()

    def _remove_whitelist(self) -> None:
        for item in self.wl_list.selectedItems():
            self.settings.whitelist.remove(item.text())
            self.wl_list.takeItem(self.wl_list.row(item))
        self._save()

    def _reset_stats(self) -> None:
        self.settings.optimize_count = 0
        self.settings.total_freed = 0
        self._save()
        self.refresh_stats()
