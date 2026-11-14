"""体检页：健康度评分 + 11 项因子明细 + 动态建议。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core import health, winmem
from core.metrics import MetricsSnapshot, fmt_speed

from . import theme_dark as T
from .components import Card, MiniBar, ScoreRing, label

LEVEL_COLORS = {
    "green": T.GREEN,
    "blue": T.ACCENT,
    "amber": T.AMBER,
    "orange": T.AMBER,
    "red": T.RED,
}


class FactorRow(QWidget):
    """一项因子：名称 + 进度条 + 得分 + 说明（+ 不达标时的「处理」入口）。"""

    go_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        # 行高封顶：11 行均分卡片高度，不封顶时每行被撑到 48px，页面看着松垮。
        # 26~30 是量出来的：再低文字贴边，再高就回到「一行一个指纹」的稀疏感。
        self.setMinimumHeight(26)
        self.setMaximumHeight(30)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)

        self.lbl_name = label("", 11, T.TEXT)
        self.lbl_name.setFixedWidth(104)
        self.bar = MiniBar(height=6)
        self.bar.setFixedWidth(58)
        self.lbl_score = label("", 10, T.TEXT_SUB)
        self.lbl_score.setFixedWidth(48)
        self.lbl_score.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_detail = label("", 10, T.TEXT_DIM)

        # 不达标才出现，平时 hide()：满了 11 行都挂一个「处理」会变成一列
        # 无意义的按钮，反而看不出哪项真的需要动手
        self.btn_go = QPushButton("处理")
        self.btn_go.setObjectName("Link")
        self.btn_go.setCursor(Qt.PointingHandCursor)
        self.btn_go.setFixedHeight(18)
        self.btn_go.clicked.connect(self._on_go)
        self._page = ""
        self.btn_go.hide()

        layout.addWidget(self.lbl_name, 0)
        layout.addWidget(self.bar, 0)
        layout.addWidget(self.lbl_score, 0)
        layout.addWidget(self.lbl_detail, 1)
        layout.addWidget(self.btn_go, 0)

    def _on_go(self) -> None:
        if self._page:
            self.go_clicked.emit(self._page)

    @staticmethod
    def _fmt_score(score: float) -> str:
        """整数就不带小数点，非整数保留一位，避免 4.55 显示成 5。"""
        rounded = round(score)
        return f"{rounded}" if abs(score - rounded) < 0.05 else f"{score:.1f}"

    def set_factor(self, factor: health.Factor, page: str = "") -> None:
        self.lbl_name.setText(factor.label)
        # 不达标（低于满分 70%，和建议的判定阈值一致）且这一项真的有处理入口，
        # 才把「处理」露出来
        self._page = page
        self.btn_go.setVisible(bool(page) and factor.measured and factor.ratio < 0.7)
        if factor.measured:
            ratio = factor.ratio
            color = T.GREEN if ratio >= 0.85 else (T.AMBER if ratio >= 0.5 else T.RED)
            self.bar.set_value(ratio, color)
            self.lbl_score.setText(
                f"{self._fmt_score(factor.score)}/{factor.weight}"
            )
            self.lbl_score.setStyleSheet(
                f"color: {color}; background: transparent; border: none;"
            )
            self.lbl_detail.setText(factor.detail)
            self.lbl_detail.setStyleSheet(
                f"color: {T.TEXT_DIM}; background: transparent; border: none;"
            )
        else:
            # 未测项不画填充条：画满会让人误以为「这项满分」
            self.bar.set_value(0.0, T.TEXT_DIM, muted=True)
            self.lbl_score.setText(f"—/{factor.weight}")
            self.lbl_score.setStyleSheet(
                f"color: {T.TEXT_DIM}; background: transparent; border: none;"
            )
            self.lbl_detail.setText(factor.detail)


class MeterBlock(QWidget):
    """一块仪表：名称 + 大数字 + 进度条 + 小字明细。

    体检页下排的「磁盘空间」和「实时负载」都由它组成。做成块而不是一行，
    是为了让多出来的高度被块自己吃掉 —— 换成固定行高的话，窗口一拉高，
    页面底部又会堆回一片空白。
    """

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(48)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self._color = ""

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(5)
        box.addStretch(1)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        self.lbl_title = label(title, 12, T.TEXT_SUB)
        self.lbl_value = label("--", 20, T.TEXT, QFont.DemiBold)
        head.addWidget(self.lbl_title)
        head.addStretch(1)
        head.addWidget(self.lbl_value)
        box.addLayout(head)

        self.bar = MiniBar(height=9)
        box.addWidget(self.bar)

        self.lbl_note = label("", 10, T.TEXT_DIM)
        box.addWidget(self.lbl_note)
        box.addStretch(1)

    def set_value(
        self, value: str, note: str, ratio: float, color: str, title: str = ""
    ) -> None:
        if title:
            self.lbl_title.setText(title)
        if self.lbl_value.text() != value:
            self.lbl_value.setText(value)
        # 每秒都刷一次，样式表只在颜色真的变了才重设：
        # setStyleSheet 会重新解析整条声明，白刷几十次没必要
        if color != self._color:
            self._color = color
            self.lbl_value.setStyleSheet(
                f"color: {color}; background: transparent; border: none;"
            )
        if self.lbl_note.text() != note:
            self.lbl_note.setText(note)
        self.bar.set_value(ratio, color)


class DiskRow(QWidget):
    """一个分区：盘符 + 文件系统 + 已用/总量，下面一条占用条。

    分区数量不定（2～5 个），做成两行的紧凑行而不是大块：块式占高太多，
    四五个分区就把下排顶出屏幕；行高跟着卡片长，拉高窗口时也不会只剩一条细线。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(42)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self._color = ""

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(5)
        box.addStretch(1)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self.lbl_mount = label("", 12, T.TEXT, QFont.DemiBold)
        self.lbl_fs = label("", 10, T.TEXT_DIM)
        self.lbl_value = label("", 11, T.TEXT_SUB)
        self.lbl_pct = label("", 12, T.TEXT, QFont.DemiBold)
        self.lbl_pct.setFixedWidth(36)
        self.lbl_pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        head.addWidget(self.lbl_mount, 0)
        head.addWidget(self.lbl_fs, 0)
        head.addStretch(1)
        head.addWidget(self.lbl_value, 0)
        head.addWidget(self.lbl_pct, 0)
        box.addLayout(head)

        self.bar = MiniBar(height=8)
        box.addWidget(self.bar)
        box.addStretch(1)

    def set_part(self, part: dict) -> None:
        percent = float(part.get("percent") or 0)
        color = T.load_color(percent)
        self.lbl_mount.setText(str(part.get("mount", "")))
        self.lbl_fs.setText(str(part.get("fs", "")))
        self.lbl_value.setText(
            f"{winmem.fmt_size(int(part.get('used', 0)))}"
            f" / {winmem.fmt_size(int(part.get('total', 0)))}"
            f" · 余 {winmem.fmt_size(int(part.get('free', 0)))}"
        )
        self.lbl_pct.setText(f"{percent:.0f}%")
        # 每秒刷一次，颜色没变就别重设样式表
        if color != self._color:
            self._color = color
            self.lbl_pct.setStyleSheet(
                f"color: {color}; background: transparent; border: none;"
            )
        self.bar.set_value(percent / 100, color)


class HealthPage(QWidget):
    # 用户点了「全面体检」，主窗口负责把垃圾/查重/探测都派出去
    full_check_requested = Signal()
    # 不达标项旁边的「处理」：让主窗口跳到对应页面（清理/查重/启动项…）
    navigate_requested = Signal(str)

    def __init__(self, store, tasks, parent=None):
        super().__init__(parent)
        self.store = store
        self.tasks = tasks
        self._report: health.HealthReport | None = None
        self._snapshot = None
        self._busy = False

        self.tasks.finished.connect(self._on_task_finished)
        self.tasks.cancelled.connect(self._on_task_cancelled)
        # 数据源变了就重算，不需要重新探测
        self.store.junk_changed.connect(self._on_inputs_changed)
        self.store.dup_changed.connect(self._on_inputs_changed)
        self.store.startup_changed.connect(self._on_inputs_changed)
        self.store.registry_changed.connect(self._on_inputs_changed)

        # 内容比一屏高（默认窗口 1100×720），套一层滚动：窗口矮时能滚，
        # 拉高时下排的仪表跟着长，两种尺寸都不会在底部堆一片空白
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
        # 右边留 8px 给滚动条，否则拉出滚动条时会压住卡片边框
        root.setContentsMargins(0, 0, 8, 0)
        root.setSpacing(7)

        top = QHBoxLayout()
        top.setSpacing(7)

        # -- 分数
        score_card = Card(padding=10, spacing=4)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(label("可清理项评分", 14, T.TEXT, QFont.DemiBold))
        head.addStretch(1)
        score_card.body().addLayout(head)

        self.ring = ScoreRing()
        # 固定尺寸 + 上下留伸展：环图居中，分数和按钮贴底，
        # 之前给环图 stretch=1，它会把卡片多余高度全吃掉，中间空一大片
        self.ring.setFixedSize(156, 156)
        score_card.body().addStretch(1)
        score_card.body().addWidget(self.ring, 0, Qt.AlignHCenter)
        score_card.body().addStretch(1)

        self.lbl_scope = label("尚未体检", 11, T.TEXT_DIM)
        self.lbl_scope.setAlignment(Qt.AlignCenter)
        self.lbl_scope.setWordWrap(True)
        score_card.body().addWidget(self.lbl_scope)

        # 总开关放这里：一颗主按钮，点一下把垃圾、查重、启动项、磁盘全测一遍
        self.btn_scan = QPushButton("全面体检")
        self.btn_scan.setObjectName("Primary")
        self.btn_scan.setFixedHeight(32)
        self.btn_scan.setCursor(Qt.PointingHandCursor)
        self.btn_scan.clicked.connect(self._on_button)
        score_card.body().addWidget(self.btn_scan)

        score_card.setFixedWidth(176)
        top.addWidget(score_card, 0)

        # -- 因子
        detail = Card(padding=10, spacing=2)
        dhead = QHBoxLayout()
        dhead.setContentsMargins(0, 0, 0, 0)
        dhead.addWidget(label("评分明细", 14, T.TEXT, QFont.DemiBold))
        dhead.addStretch(1)
        self.lbl_detail_note = label("", 10, T.TEXT_DIM)
        dhead.addWidget(self.lbl_detail_note)
        detail.body().addLayout(dhead)

        self.rows: list[FactorRow] = []
        for _ in range(11):
            row = FactorRow()
            row.go_clicked.connect(self.navigate_requested.emit)
            detail.body().addWidget(row)
            self.rows.append(row)
        # 行封顶后卡片高度就由内容决定了，不要再来一次拉伸
        top.addWidget(detail, 1)

        legend = Card(padding=10, spacing=2)
        legend.body().addWidget(label("等级划分", 14, T.TEXT, QFont.DemiBold))
        legend.body().addSpacing(2)
        for name, color, span in (
            ("优秀", T.GREEN, "≥ 85"),
            ("良好", T.ACCENT, "70 – 84"),
            ("一般", T.AMBER, "50 – 69"),
            ("较差", "#E08A2B", "30 – 49"),
            ("危险", T.RED, "< 30"),
        ):
            row = QWidget()
            row.setMinimumHeight(21)
            row.setMaximumHeight(32)
            row.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(7)
            dot = QLabel()
            dot.setFixedSize(8, 8)
            dot.setStyleSheet(f"background: {color}; border-radius: 4px;")
            rl.addWidget(dot, 0)
            rl.addWidget(label(name, 12, T.TEXT_SUB), 0)
            rl.addStretch(1)
            rl.addWidget(label(span, 11, T.TEXT_DIM), 0)
            legend.body().addWidget(row)
        # 5 个等级贴上排，说明文字贴底，中间多出来的高度不用摊进行距
        legend.body().addStretch(1)
        note = label(
            "灰色项需要先执行对应扫描（常规清理、重复文件）才会纳入评分。",
            11,
            T.TEXT_DIM,
        )
        note.setWordWrap(True)
        legend.body().addWidget(note)
        legend.setFixedWidth(152)
        top.addWidget(legend, 0)

        # 不拉伸上排：卡片高度跟内容走，多余高度留到页面底部，
        # 否则整页被拉满，11 行明细在卡片里吊在顶上，下半张全空
        root.addLayout(top, 0)

        # -- 建议
        advice = Card(padding=10, spacing=4)
        ahead = QHBoxLayout()
        ahead.setContentsMargins(0, 0, 0, 0)
        ahead.addWidget(label("优化建议", 14, T.TEXT, QFont.DemiBold))
        ahead.addStretch(1)
        ahead.addWidget(label("按得分低的项生成", 10, T.TEXT_DIM))
        advice.body().addLayout(ahead)

        self.advice_box = QVBoxLayout()
        self.advice_box.setSpacing(2)
        advice.body().addLayout(self.advice_box)
        advice.body().addStretch(1)
        self._advice_rows: list[QWidget] = []
        root.addWidget(advice, 0)

        root.addLayout(self._build_overview(), 1)

    def _build_overview(self) -> QHBoxLayout:
        """下排：磁盘分区 + 实时负载。

        这两块原来一直是空的 —— 上排三张卡加建议只占半屏，窗口一拉高，
        下半屏就是一整片底色。数据全部来自每秒下发的 `MetricsSnapshot`，
        不额外开任务，也不重新扫盘。
        """
        row = QHBoxLayout()
        row.setSpacing(7)

        # -- 磁盘
        disk_card = Card(padding=10, spacing=4)
        dhead = QHBoxLayout()
        dhead.setContentsMargins(0, 0, 0, 0)
        dhead.addWidget(label("磁盘空间", 14, T.TEXT, QFont.DemiBold))
        dhead.addStretch(1)
        self.lbl_disk_sum = label("正在读取…", 10, T.TEXT_DIM)
        dhead.addWidget(self.lbl_disk_sum)
        disk_card.body().addLayout(dhead)

        self._disk_box = QVBoxLayout()
        self._disk_box.setSpacing(4)
        disk_card.body().addLayout(self._disk_box)
        self._disk_rows: list[DiskRow] = []
        # 分区列表变了才重建行，平时只改文字
        self._disk_mounts: tuple[str, ...] = ()
        # 分区多的时候，整机还剩多少得有个合计
        self.blk_total = MeterBlock("全部分区剩余")
        disk_card.body().addWidget(self.blk_total)
        row.addWidget(disk_card, 1)

        # -- 实时负载
        load_card = Card(padding=10, spacing=4)
        lhead = QHBoxLayout()
        lhead.setContentsMargins(0, 0, 0, 0)
        lhead.addWidget(label("实时负载", 14, T.TEXT, QFont.DemiBold))
        lhead.addStretch(1)
        lhead.addWidget(label("每秒刷新", 10, T.TEXT_DIM))
        load_card.body().addLayout(lhead)

        self.blk_cpu = MeterBlock("CPU 使用率")
        self.blk_mem = MeterBlock("内存占用")
        self.blk_gpu = MeterBlock("显卡占用")
        self.blk_io = MeterBlock("磁盘读写")
        self.blk_net = MeterBlock("网络")
        for block in (self.blk_cpu, self.blk_mem, self.blk_gpu,
                      self.blk_io, self.blk_net):
            load_card.body().addWidget(block)
        self.blk_gpu.hide()
        load_card.setFixedWidth(250)
        row.addWidget(load_card, 0)

        return row

    # ---------------------------------------------------------- 数据

    def _set_busy(self, busy: bool) -> None:
        """扫描中把按钮变成「取消」。"""
        self._busy = busy
        self.btn_scan.setText("取消" if busy else "全面体检")
        self.btn_scan.setEnabled(True)

    def _on_button(self) -> None:
        if self._busy:
            self.cancel_scan()
        else:
            self.full_check_requested.emit()

    # -- 外部入口（主窗口编排「全面体检」时调用）

    def start_probe(self) -> None:
        """只探测系统侧（磁盘/启动项/内存），几十毫秒。"""
        if self.tasks.is_busy("health_probe"):
            return
        self._set_busy(True)
        self.lbl_scope.setText("正在探测系统…")
        self.tasks.submit("health_probe", "探测系统状态", self._probe_task)

    def cancel_scan(self) -> None:
        self.tasks.cancel("health_probe")
        self.btn_scan.setText("正在取消…")
        self.btn_scan.setEnabled(False)

    def _probe_task(self, report, should_stop):
        report(30, "正在读取系统盘…")
        snapshot = health.probe_system(should_stop)
        if snapshot is None:
            return None
        report(90, "正在汇总…")
        return snapshot

    # -- 任务与数据变化

    def _on_task_finished(self, task_id: str, result) -> None:
        if task_id != "health_probe":
            return
        self._set_busy(False)
        if result is None:
            self.lbl_scope.setText("探测失败，请稍后重试")
            return
        self._snapshot = result
        self._recompute()

    def _on_task_cancelled(self, task_id: str) -> None:
        if task_id == "health_probe":
            self._set_busy(False)
            self.lbl_scope.setText("已取消")

    def _on_inputs_changed(self) -> None:
        """垃圾/重复/启动项的数据变了，重算分数。

        这是这次重构的核心：清理页扫完，体检页立刻跟着变，
        不需要再点「全面体检」重新扫一遍。
        """
        if self._snapshot is None:
            return
        self._recompute()

    def _recompute(self) -> None:
        report = health.collect(
            snapshot=self._snapshot, **self.store.health_inputs()
        )
        if report is None:
            return
        self._report = report
        self._render(report)

    def shutdown(self) -> None:
        self.tasks.cancel_many("health_probe")

    # ---------------------------------------------------------- 实时数据

    def update_metrics(self, snap: MetricsSnapshot) -> None:
        """主窗口每秒调一次。只改文字和进度条，不重建控件。"""
        self._sync_disk(snap.disk)

        cpu = snap.cpu
        self.blk_cpu.set_value(
            f"{cpu.total:.0f}%",
            f"{cpu.logical} 线程 · {cpu.freq_mhz:.0f} MHz",
            cpu.total / 100,
            T.load_color(cpu.total),
        )

        mem = snap.memory
        self.blk_mem.set_value(
            f"{mem.percent:.0f}%",
            f"{winmem.fmt_size(mem.used)} / {winmem.fmt_size(mem.total)}"
            f" · 可用 {winmem.fmt_size(mem.avail)}",
            mem.percent / 100,
            T.load_color(mem.percent),
        )

        disk = snap.disk
        # 读写速率没有「满格」概念，按 200 MB/s 当量程画条，超出就顶格
        io_ratio = max(disk.read_bps, disk.write_bps) / (200 * 1024 * 1024)
        self.blk_io.set_value(
            f"读 {fmt_speed(disk.read_bps)}",
            f"写 {fmt_speed(disk.write_bps)} · 实时",
            io_ratio,
            T.ACCENT,
        )

        net = snap.network
        # 同理：100 Mbps 宽带满速约 12.5 MB/s，按这个当量程
        net_ratio = max(net.down_bps, net.up_bps) / (12.5 * 1024 * 1024)
        self.blk_net.set_value(
            f"↓ {fmt_speed(net.down_bps)}",
            f"↑ {fmt_speed(net.up_bps)} · 实时",
            net_ratio,
            T.ACCENT,
        )

        # 集显机器没有 nvidia-smi，这块直接收起来，不留一个「--」
        gpu = snap.gpu
        self.blk_gpu.setVisible(gpu is not None)
        if gpu is not None:
            self.blk_gpu.set_value(
                f"{gpu.utilization:.0f}%",
                f"显存 {winmem.fmt_size(gpu.memory_used)}"
                f" / {winmem.fmt_size(gpu.memory_total)}",
                gpu.utilization / 100,
                T.load_color(gpu.utilization),
            )

    def _sync_disk(self, disk) -> None:
        """分区列表几乎不变，只有挂载点变了才重建行，其余时候只改数值。"""
        parts = list(getattr(disk, "partitions", ()) or ())
        mounts = tuple(str(p.get("mount", "")) for p in parts)
        if mounts != self._disk_mounts:
            for row in self._disk_rows:
                row.setParent(None)
                row.deleteLater()
            self._disk_rows.clear()
            for _ in parts:
                row = DiskRow()
                self._disk_box.addWidget(row)
                self._disk_rows.append(row)
            self._disk_mounts = mounts

        for row, part in zip(self._disk_rows, parts):
            row.set_part(part)

        total = sum(int(p.get("total", 0)) for p in parts)
        used = sum(int(p.get("used", 0)) for p in parts)
        self.lbl_disk_sum.setText(f"{len(parts)} 个分区" if parts else "")
        if total:
            ratio = used / total
            self.blk_total.set_value(
                winmem.fmt_size(total - used),
                f"共 {winmem.fmt_size(total)} · 已用 {winmem.fmt_size(used)}"
                f"（{ratio * 100:.0f}%）",
                ratio,
                T.load_color(ratio * 100),
            )


    def _render(self, report: health.HealthReport) -> None:
        # 只展示「靠清理能改善」的项。硬件三项（容量/磁盘/文件系统）已经在
        # 系统信息的「硬件健康」卡片里 —— 它们改不了，混进来只会让 72 分
        # 被读成「硬盘要坏了」，其实只是垃圾多。
        factors = report.cleanup_factors
        score = report.cleanup_score
        level, color_key = health.level_of(score)
        color = LEVEL_COLORS.get(color_key, T.ACCENT)
        self.ring.set_score(score, level, color)

        measured = sum(1 for f in factors if f.measured)
        self.lbl_scope.setText(
            f"已测 {measured}/{len(factors)} 项 · 硬件状态见「系统信息」"
        )
        self.lbl_detail_note.setText(
            "灰色项扫描后自动补齐"
            if measured < len(factors)
            else "全部指标已接入"
        )

        for index, row in enumerate(self.rows):
            if index < len(factors):
                factor = factors[index]
                row.show()
                row.set_factor(factor, health.FACTOR_PAGE.get(factor.key, ""))
            else:
                row.hide()

        for widget in self._advice_rows:
            widget.deleteLater()
        self._advice_rows.clear()

        for item in report.advice:
            line = QWidget()
            line_layout = QHBoxLayout(line)
            line_layout.setContentsMargins(0, 0, 0, 0)
            line_layout.setSpacing(7)
            dot = label("•", 11, T.ACCENT)
            dot.setFixedWidth(8)
            body = label(item.text, 11, T.TEXT_SUB)
            body.setWordWrap(True)
            line_layout.addWidget(dot, 0, Qt.AlignTop)
            line_layout.addWidget(body, 1)
            if item.page:
                # 有入口的建议才给按钮：磁盘告警这类点进去也做不了什么
                btn = QPushButton("去处理")
                btn.setObjectName("Link")
                btn.setCursor(Qt.PointingHandCursor)
                btn.setFixedHeight(20)
                btn.clicked.connect(
                    lambda _checked=False, target=item.page: (
                        self.navigate_requested.emit(target)
                    )
                )
                line_layout.addWidget(btn, 0, Qt.AlignTop)
            self.advice_box.addWidget(line)
            self._advice_rows.append(line)
