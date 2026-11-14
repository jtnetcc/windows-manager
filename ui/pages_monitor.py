"""监控类页面：概览、CPU、内存、磁盘、网络，以及把它们收进一个入口的壳。

统一版式，尽量把信息压进一屏：
    4 个 KPI 卡（高 66）
    图表 | 明细  两栏
    可选一行排行列表
所有页面都实现 update_metrics(snap)；需要进程数据的页面再加
update_processes(rows, total, leaks)。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core import winmem
from core.metrics import MetricsSnapshot, fmt_bytes, fmt_speed

from . import theme_dark as T
from .components import (
    Card,
    CoreTiles,
    Dot,
    KpiCard,
    RankList,
    SegmentBar,
    Segmented,
    TrendChart,
    label,
)
from .feed import feed
from .page_process import ProcessPage
from core import applog

KPI_HEIGHT = 66

# 监控组的分段：一个导航入口，页内切这 6 个视图
SEGMENTS = [
    ("overview", "总览"),
    ("cpu", "CPU"),
    ("memory", "内存"),
    ("disk", "磁盘"),
    ("network", "网络"),
    ("process", "进程"),
]

SEGMENT_KEYS = {key for key, _text in SEGMENTS}

SEGMENT_SUBS = {
    "overview": "实时监控 CPU、内存、磁盘与网络",
    "cpu": "处理器占用、频率与核心负载",
    "memory": "内存构成、缓存回收与占用大户",
    "disk": "容量、读写速率、分区占用与磁盘健康",
    "network": "吞吐速率与网络接口状态",
    "process": "按占用排序的进程列表，右键可结束进程",
}

# 每个分段右上角的补充说明，空了就不显示
SEGMENT_NOTES = {
    "disk": "SMART 首次进入时读取",
    "process": "进程 3 秒刷新 · 右键操作",
}


def _kpi_row(items: list[KpiCard]) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(8)
    for item in items:
        item.setFixedHeight(KPI_HEIGHT)
        row.addWidget(item, 1)
    return row


def _two_col(left: QWidget, right: QWidget, ratio: tuple[int, int] = (2, 1)) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(9)
    row.addWidget(left, ratio[0])
    row.addWidget(right, ratio[1])
    return row


def _card_title(text: str, note: str = "") -> tuple[Card, QWidget]:
    """返回 (卡片, 右上角的备注标签)。"""
    card = Card(padding=12, spacing=7)
    head = QHBoxLayout()
    head.setContentsMargins(0, 0, 0, 0)
    head.setSpacing(6)
    head.addWidget(label(text, 14, T.TEXT, QFont.DemiBold))
    head.addStretch(1)
    note_label = label(note, 10, T.TEXT_DIM)
    head.addWidget(note_label)
    card.body().addLayout(head)
    return card, note_label


def _freq_text(mhz: float) -> str:
    """频率文案：过 1 GHz 换成 GHz，四位数的 MHz 在概览的窄卡里太长。"""
    if mhz >= 1000:
        return f"{mhz / 1000:.1f} GHz"
    return f"{mhz:.0f} MHz" if mhz > 0 else "—"


def _uptime_text(seconds: float) -> str:
    """开机时长。到天后只说「天 + 小时」，分钟级别在这里没人看。"""
    total = int(max(0.0, seconds))
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days} 天 {hours} 小时"
    if hours:
        return f"{hours} 小时 {minutes} 分"
    return f"{minutes} 分钟"


def _boot_time() -> float:
    """本次开机时刻。读不到就按「现在」算，宁可显示 0 分钟也别抛异常。"""
    import time

    try:
        import psutil

        return psutil.boot_time()
    except Exception:
        return time.time()


def _os_short() -> str:
    """系统简称，例如「Win11 24H2」。

    概览的概况格子只有一百多像素宽，产品全名（Windows 11 家庭中文版）
    在那儿会被省略号吃掉；换成「Win11 + 版本号」读起来信息密度更高。
    """
    import winreg

    try:
        path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            product = str(winreg.QueryValueEx(key, "ProductName")[0])
            build = str(winreg.QueryValueEx(key, "CurrentBuild")[0])
            try:
                display = str(winreg.QueryValueEx(key, "DisplayVersion")[0])
            except OSError:
                display = ""
    except OSError:
        return ""

    # Win11 的 ProductName 里仍写着「Windows 10」，只能靠 build 号判断
    if build.isdigit() and int(build) >= 22000:
        family = "Win11"
    elif "Windows 10" in product:
        family = "Win10"
    else:
        family = product
    return f"{family} {display}".strip()


def _core_note(cpu) -> str:
    """每核卡片右上角的补充说明。

    这里原来写「4 物理 / 4 逻辑」，和同一屏 KPI 卡上的核数重复了一遍；
    改成统计量 —— 它回答的是「这几个核现在忙不忙」，核数交给 KPI 说。
    """
    values = cpu.per_core
    if not values:
        return ""
    return f"平均 {sum(values) / len(values):.0f}% · 峰值 {max(values):.0f}%"


def _top_by_name(rows: list[dict], value_key: str, limit: int = 6) -> list[tuple]:
    """按进程名合并后取前 N 名。

    浏览器、Electron 应用会开出十几个同名进程，单独列出会占满整张榜，
    合并成「WorkBuddyAI.exe ×8」才看得出到底是谁吃资源。
    """
    grouped: dict[str, list] = {}
    for row in rows:
        entry = grouped.get(row["name"])
        if entry is None:
            grouped[row["name"]] = [float(row.get(value_key, 0) or 0), 1]
        else:
            entry[0] += float(row.get(value_key, 0) or 0)
            entry[1] += 1
    ordered = sorted(grouped.items(), key=lambda kv: kv[1][0], reverse=True)
    return [(name, count, total) for name, (total, count) in ordered[:limit]]


class ActionStrip(Card):
    """待处理事项：一行可点击的建议，点了直接跳到对应页面。

    概览页原来只有「看」没有「做」，用户看完还得自己找页面。
    这里把 store 里的结果转成动作，一眼就知道下一步该干嘛。
    """

    MAX_ITEMS = 4

    def __init__(self, on_navigate, parent=None):
        super().__init__(parent, padding=12, spacing=7)
        self._navigate = on_navigate
        self._row = QHBoxLayout()
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(8)

        # 标题和按钮排在同一行：监控页多了一条分段栏，竖向空间要省着用
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(12)
        head.addWidget(label("待处理事项", 14, T.TEXT, QFont.DemiBold))
        head.addLayout(self._row, 1)
        self.lbl_note = label("", 10, T.TEXT_DIM)
        head.addWidget(self.lbl_note)
        self.body().addLayout(head)

        self._buttons: list[QPushButton] = []

    def refresh(self, items: list[tuple[str, str, bool]]) -> None:
        """items: [(文案, 页面 key, 是否强调)]"""
        # 必须把布局清空重来：只删按钮的话，每次 refresh 都会多留一个
        # addStretch，几次之后按钮就被挤到右边去了
        while self._row.count():
            item = self._row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._buttons.clear()

        if not items:
            empty = label("暂无待处理项，系统状态良好", 12, T.TEXT_DIM)
            self._row.addWidget(empty)
            self._row.addStretch(1)
            self.lbl_note.setText("")
            return

        for text, page_key, emphasis in items[: self.MAX_ITEMS]:
            button = QPushButton(text)
            button.setObjectName("Primary" if emphasis else "Ghost")
            button.setFixedHeight(30)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, key=page_key: self._navigate(key)
            )
            self._row.addWidget(button)
            self._buttons.append(button)
        self._row.addStretch(1)
        self.lbl_note.setText(f"{len(items)} 项")


class OverviewPage(QWidget):
    """总览：一屏说清「机器忙不忙、被谁占着、有什么要处理」。

    版式三行：KPI 四连 → 趋势图 + 内存构成/系统概况 → 两张排行榜 → 待处理事项。

    每核占用只在 CPU 段出现。之前两块都放了同一张 CoreTiles，等于把
    「哪几个核在忙」看两遍，还挤掉了概览该有的内存构成；现在这块地盘
    换成了内存构成和系统概况 —— 一个回答「内存被谁吃了」，一个回答
    「这台机器开了多久、跑着什么版本」，和 CPU 段不重叠。
    """

    def __init__(self, store=None, on_navigate=None, parent=None):
        super().__init__(parent)
        self.store = store
        self._boot = _boot_time()
        self._os = _os_short()
        self._sys_rows: list[tuple] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        # 分段栏吃掉了 41px，概览页的三段间距收一档才能在小窗口里放完
        root.setSpacing(7)

        self.kpi_cpu = KpiCard("CPU", "%", "cpu")
        self.kpi_mem = KpiCard("内存", "%", "memory")
        self.kpi_disk = KpiCard("磁盘", "%", "disk")
        self.kpi_net = KpiCard("网络", "", "network")
        root.addLayout(
            _kpi_row([self.kpi_cpu, self.kpi_mem, self.kpi_disk, self.kpi_net])
        )

        # 左：CPU + 内存画在同一张图上。总览要回答的是「整机忙不忙」，
        # 两条线叠在一起，内存跟着 CPU 一起涨的那种卡顿才看得出来。
        trend, self.lbl_trend = _card_title("系统趋势", "CPU 与内存 · 最近 3 分钟")
        self.chart = TrendChart(
            [("CPU", T.ACCENT), ("内存", T.PURPLE)], capacity=180
        )
        trend.add(self.chart, 1)

        # 右列：内存构成 + 系统概况。两块都是「一句话说清现状」的短卡片，
        # 上下叠起来正好和左边趋势图一样高。
        column = QWidget()
        col = QVBoxLayout(column)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(7)

        mem_card, self.lbl_mem_total = _card_title("内存构成")
        self.mem_bar = SegmentBar(height=9)
        mem_card.add(self.mem_bar)

        # 三段并排的小统计，而不是内存页那种「一行一条」的图例：
        # 概览这一格只有三十来字的空间，竖排三条会撑出卡片；
        # 横排三列也顺带和内存页的版式区分开，切过去不至于像看同一张卡。
        stats = QHBoxLayout()
        stats.setContentsMargins(0, 1, 0, 0)
        stats.setSpacing(8)
        self._mem_values: list[QLabel] = []
        for title, color in zip(("使用中", "待命", "空闲"), T.mem_colors()):
            cell = QVBoxLayout()
            cell.setContentsMargins(0, 0, 0, 0)
            cell.setSpacing(1)

            name_row = QHBoxLayout()
            name_row.setContentsMargins(0, 0, 0, 0)
            name_row.setSpacing(5)
            dot = QLabel()
            dot.setFixedSize(6, 6)
            dot.setStyleSheet(f"background: {color}; border-radius: 3px;")
            name_row.addWidget(dot, 0, Qt.AlignVCenter)
            name_row.addWidget(label(title, 10, T.TEXT_DIM))
            name_row.addStretch(1)
            cell.addLayout(name_row)

            value = label("--", 13, T.TEXT, QFont.DemiBold)
            cell.addWidget(value)
            self._mem_values.append(value)

            stats.addLayout(cell, 1)
        mem_card.body().addLayout(stats)
        mem_card.body().addStretch(1)

        sys_card, _ = _card_title("系统概况")
        self.sys_rank = RankList(rows=4, elide_width=100)
        sys_card.add(self.sys_rank, 1)

        col.addWidget(mem_card, 1)
        col.addWidget(sys_card, 1)

        root.addLayout(_two_col(trend, column), 6)

        mem_top, _ = _card_title("内存占用 Top")
        self.mem_rank = RankList(rows=4, elide_width=130)
        mem_top.add(self.mem_rank, 1)

        part_card, self.lbl_parts = _card_title("磁盘分区")
        self.part_rank = RankList(rows=4, elide_width=110)
        part_card.add(self.part_rank, 1)
        root.addLayout(_two_col(mem_top, part_card), 3)

        self.actions = ActionStrip(on_navigate or (lambda _key: None))
        root.addWidget(self.actions)

        if store is not None:
            for signal in (
                store.junk_changed,
                store.dup_changed,
                store.registry_changed,
                store.startup_changed,
            ):
                signal.connect(self.refresh_actions)
            self.refresh_actions()

    def refresh_actions(self) -> None:
        """把 store 里的结果翻译成「去做什么」。"""
        items: list[tuple[str, str, bool]] = []
        store = self.store
        if store is None:
            return

        if store.junk.ready and store.junk.total > 0:
            items.append((f"垃圾 {fmt_bytes(store.junk.total)} · 去清理", "clean", False))
        if store.dup.ready and store.dup.wasted > 0:
            items.append((f"重复文件 {fmt_bytes(store.dup.wasted)} · 去查重", "dup", False))
        if store.registry.ready and store.registry.issues:
            items.append(
                (f"注册表 {len(store.registry.issues)} 项残留 · 去处理", "registry", False)
            )
        if store.startup.ready and store.startup.enabled > 8:
            items.append(
                (f"启动项 {store.startup.enabled} 个 · 去精简", "startup", False)
            )

        try:
            from core import trash

            size = trash.total_size()
            if size > 1024 ** 3:
                items.append((f"回收站 {fmt_bytes(size)} · 去查看", "recycle", False))
        except Exception:
            applog.silenced("pages_monitor.refresh_actions")

        if not items:
            items.append(("做一次全面体检", "__full_check__", True))

        self.actions.refresh(items)

    def update_metrics(self, snap: MetricsSnapshot) -> None:
        cpu = snap.cpu
        mem = snap.memory
        total = mem.total or 1

        # CPU 副标题只说频率和峰值，不再写核数：核数归 CPU 段的「核心」卡，
        # 这边再说一遍就是同一句话读两次
        peak = max(cpu.per_core) if cpu.per_core else 0.0
        self.kpi_cpu.set_value(
            f"{cpu.total:.0f}",
            f"{_freq_text(cpu.freq_mhz)} · 峰值 {peak:.0f}%",
            T.load_color(cpu.total),
            meter=cpu.total / 100,
            meter_color=T.load_color(cpu.total),
        )
        self.kpi_mem.set_value(
            f"{mem.percent:.0f}",
            f"可用 {fmt_bytes(mem.avail)}",
            T.load_color(mem.percent),
            meter=mem.percent / 100,
            meter_color=T.load_color(mem.percent),
        )
        self.kpi_disk.set_value(
            f"{snap.disk.percent:.0f}",
            f"剩余 {fmt_bytes(snap.disk.free)}",
            T.load_color(snap.disk.percent),
            meter=snap.disk.percent / 100,
            meter_color=T.load_color(snap.disk.percent),
        )
        self.kpi_net.set_value(
            f"↓{fmt_speed(snap.network.down_bps).replace(' ', '')}",
            f"↑{fmt_speed(snap.network.up_bps)}",
        )
        self.chart.push([cpu.total, mem.percent])

        self.lbl_mem_total.setText(f"共 {mem.total / winmem.GB:.1f} GB")
        used_color, cache_color, free_color = T.mem_colors()
        self.mem_bar.set_segments(
            [
                (mem.used / total, used_color),
                (mem.cache / total, cache_color),
                (mem.free / total, free_color),
            ]
        )
        self._mem_values[0].setText(fmt_bytes(mem.used))
        self._mem_values[1].setText(fmt_bytes(mem.cache))
        self._mem_values[2].setText(fmt_bytes(mem.free))

        self._fill_system(snap)
        self._fill_partitions(snap.disk.partitions)

    def _fill_system(self, snap: MetricsSnapshot) -> None:
        """系统概况：开机时长、进程/线程、本机 IP、系统版本。

        这几项每秒只有开机时长在动（还是一分钟才变一次），所以整组文案
        不变就不写控件 —— RankList.set_rows 每行都要 setText，白跑一遍没意义。
        """
        import time

        ip = ""
        for item in snap.network.interfaces:
            if item.get("ip"):
                ip = item["ip"]
                break

        mem = snap.memory
        rows = [
            ("开机时长", "", _uptime_text(time.time() - self._boot)),
            ("进程 / 线程", "", f"{mem.process_count} / {mem.thread_count}"),
            ("本机 IP", "", ip or "—"),
            ("系统", "", self._os or "—"),
        ]
        if rows == self._sys_rows:
            return
        self._sys_rows = rows
        self.sys_rank.set_rows(rows)

    def _fill_partitions(self, partitions) -> None:
        rows = []
        for part in partitions[:5]:
            rows.append(
                (
                    f"{part['mount']}",
                    part["fs"],
                    f"{part['percent']:.0f}% · 余 {fmt_bytes(part['free'])}",
                    T.load_color(part["percent"]),
                )
            )
        self.part_rank.set_rows(rows)
        self.lbl_parts.setText(f"{len(partitions)} 个分区")

    def update_processes(self, rows: list[dict], total: int, leaks: set[int]) -> None:
        items = []
        for name, count, memory in _top_by_name(rows, "memory", 5):
            items.append(
                (
                    name,
                    f"×{count}" if count > 1 else "",
                    fmt_bytes(memory),
                    T.AMBER if any(
                        r["pid"] in leaks for r in rows if r["name"] == name
                    ) else T.TEXT_SUB,
                )
            )
        self.mem_rank.set_rows(items)


class CpuPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(9)

        self.kpi_total = KpiCard("总占用", "%", "cpu")
        self.kpi_freq = KpiCard("当前频率", " MHz", "bolt")
        self.kpi_cores = KpiCard("核心", "", "cpu")
        self.kpi_load = KpiCard("负载均值", "", "health")
        root.addLayout(
            _kpi_row(
                [self.kpi_total, self.kpi_freq, self.kpi_cores, self.kpi_load]
            )
        )

        trend, self.lbl_model = _card_title("CPU 使用率")
        self.chart = TrendChart([("CPU", T.ACCENT)], capacity=180)
        trend.add(self.chart, 1)

        cores, self.lbl_cores = _card_title("每核占用")
        self.core_tiles = CoreTiles()
        cores.add(self.core_tiles, 1)
        root.addLayout(_two_col(trend, cores), 6)

        top_card, self.lbl_top_cpu = _card_title("CPU 占用 Top")
        self.cpu_rank = RankList(rows=5, elide_width=220)
        top_card.add(self.cpu_rank, 1)
        root.addWidget(top_card, 3)

    def update_metrics(self, snap: MetricsSnapshot) -> None:
        cpu = snap.cpu
        self.kpi_total.set_value(f"{cpu.total:.0f}", "实时", T.load_color(cpu.total))
        base = cpu.model.split("@")[-1].strip() if "@" in cpu.model else "—"
        self.kpi_freq.set_value(f"{cpu.freq_mhz:.0f}", f"基准 {base}")
        # 主数值已经写了逻辑处理器数，副标题只在真有超线程时才补物理核
        self.kpi_cores.set_value(
            f"{cpu.logical}",
            f"{cpu.physical} 物理核" if cpu.physical != cpu.logical else "无超线程",
        )
        self.kpi_load.set_value(
            f"{cpu.load_avg[0]:.2f}", f"5 分钟 {cpu.load_avg[1]:.2f}"
        )
        self.chart.push([cpu.total])
        self.core_tiles.set_values(list(cpu.per_core))
        self.lbl_model.setText(cpu.model)
        self.lbl_cores.setText(_core_note(cpu))

    def update_processes(self, rows: list[dict], total: int, leaks: set[int]) -> None:
        items = []
        for name, count, cpu in _top_by_name(rows, "cpu", 5):
            items.append(
                (
                    name,
                    f"×{count}" if count > 1 else "",
                    f"{cpu:.1f}%",
                    T.AMBER if cpu >= 25 else T.TEXT_SUB,
                )
            )
        self.cpu_rank.set_rows(items)
        hot = sum(1 for r in rows if r.get("cpu", 0) >= 25)
        self.lbl_top_cpu.setText(f"{hot} 个进程高于 25%" if hot else "按 CPU 排序")


class MemoryPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(9)

        self.kpi_used = KpiCard("使用中", "", "memory")
        self.kpi_cache = KpiCard("可回收缓存", "", "clean")
        self.kpi_free = KpiCard("可用内存", "", "memory")
        self.kpi_commit = KpiCard("提交内存", "", "file")
        root.addLayout(
            _kpi_row(
                [self.kpi_used, self.kpi_cache, self.kpi_free, self.kpi_commit]
            )
        )

        trend, _ = _card_title("内存使用率", "最近 3 分钟")
        self.chart = TrendChart([("内存", T.ACCENT)], capacity=180)
        trend.add(self.chart, 1)

        detail, _ = _card_title("内存构成")
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        self.lbl_status = label("尚未优化", 10, T.TEXT_DIM)
        head.addWidget(self.lbl_status)
        head.addStretch(1)
        self.lbl_total = label("", 10, T.TEXT_DIM)
        head.addWidget(self.lbl_total)
        detail.body().addLayout(head)

        self.bar = SegmentBar(height=10)
        detail.add(self.bar)

        used_color, cache_color, free_color = T.mem_colors()
        self.dot_used = Dot(used_color, "使用中")
        self.dot_cache = Dot(cache_color, "待命")
        self.dot_free = Dot(free_color, "空闲")
        for dot in (self.dot_used, self.dot_cache, self.dot_free):
            detail.body().addWidget(dot)

        self.lbl_hint = label("", 10, T.TEXT_DIM)
        self.lbl_hint.setWordWrap(True)
        detail.body().addWidget(self.lbl_hint)

        # 这里不再放第二个「一键优化」按钮：和标题栏那个调的是同一个
        # run_optimize()，同屏两个蓝色主按钮只是让人犹豫点哪个。
        detail.body().addStretch(1)

        guide = label("清理缓存用右上角「一键优化」", 10, T.TEXT_DIM)
        guide.setAlignment(Qt.AlignCenter)
        detail.body().addWidget(guide)

        root.addLayout(_two_col(trend, detail), 6)

        bottom = QHBoxLayout()
        bottom.setSpacing(9)

        mem_card, self.lbl_mem_top = _card_title("内存占用 Top")
        self.mem_rank = RankList(rows=4, elide_width=140)
        mem_card.add(self.mem_rank, 1)

        leak_card, self.lbl_leak = _card_title("疑似泄漏")
        self.leak_rank = RankList(rows=4, elide_width=130)
        leak_card.add(self.leak_rank, 1)

        bottom.addWidget(mem_card, 1)
        bottom.addWidget(leak_card, 1)
        root.addLayout(bottom, 3)

    def update_metrics(self, snap: MetricsSnapshot) -> None:
        mem = snap.memory
        total = mem.total or 1
        self.kpi_used.set_value(
            fmt_bytes(mem.used), f"{mem.percent:.0f}%", T.load_color(mem.percent)
        )
        self.kpi_cache.set_value(fmt_bytes(mem.cache), "可随时回收")
        self.kpi_free.set_value(fmt_bytes(mem.avail), f"其中空闲 {fmt_bytes(mem.free)}")
        self.kpi_commit.set_value(
            fmt_bytes(mem.commit_used), f"上限 {fmt_bytes(mem.commit_limit)}"
        )
        self.chart.push([mem.percent])
        used_color, cache_color, free_color = T.mem_colors()
        self.bar.set_segments(
            [
                (mem.used / total, used_color),
                (mem.cache / total, cache_color),
                (mem.free / total, free_color),
            ]
        )
        self.dot_used.set_value(fmt_bytes(mem.used))
        self.dot_cache.set_value(fmt_bytes(mem.cache))
        self.dot_free.set_value(fmt_bytes(mem.free))
        self.lbl_total.setText(f"共 {mem.total / winmem.GB:.1f} GB")
        self.lbl_hint.setText(self._hint(mem))

    def update_processes(self, rows: list[dict], total: int, leaks: set[int]) -> None:
        items = []
        for name, count, memory in _top_by_name(rows, "memory", 4):
            items.append(
                (name, f"×{count}" if count > 1 else "", fmt_bytes(memory))
            )
        self.mem_rank.set_rows(items)

        leak_rows = [r for r in rows if r["pid"] in leaks]
        leak_rows.sort(key=lambda r: r["memory"], reverse=True)
        self.leak_rank.set_rows(
            [
                (r["name"], f"PID {r['pid']}", fmt_bytes(r["memory"]), T.AMBER)
                for r in leak_rows[:4]
            ]
        )
        self.lbl_leak.setText(
            f"{len(leak_rows)} 个可疑" if leak_rows else "未发现异常增长"
        )

    @staticmethod
    def _hint(mem) -> str:
        if mem.percent >= 85:
            return "内存压力较大，建议立即清理一次待命缓存。"
        if mem.cache >= winmem.GB:
            return f"有 {fmt_bytes(mem.cache)} 待命缓存可回收，清理后立刻转为可用内存。"
        return "当前占用平稳，待命缓存会随系统需要自动回收。"

    def set_status(self, text: str, color: str = T.TEXT_DIM) -> None:
        self.lbl_status.setText(text)
        self.lbl_status.setStyleSheet(
            f"color: {color}; background: transparent; border: none;"
        )


class DiskPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(9)

        self.kpi_total = KpiCard("总容量", "", "disk")
        self.kpi_used = KpiCard("已使用", "", "disk")
        self.kpi_read = KpiCard("读取", "", "file")
        self.kpi_write = KpiCard("写入", "", "file")
        root.addLayout(
            _kpi_row(
                [self.kpi_total, self.kpi_used, self.kpi_read, self.kpi_write]
            )
        )

        trend, self.lbl_mount = _card_title("磁盘读写速率", "KB/s")
        self.chart = TrendChart(
            [("读取", T.ACCENT), ("写入", T.PURPLE)], capacity=180, unit=""
        )
        trend.add(self.chart, 1)
        root.addWidget(trend, 6)

        # 磁盘健康（SMART）和分区并列。
        # 这块数据一直都有，但以前只在体检里用来算分，界面上看不到。
        health_card, self.lbl_health = _card_title("磁盘健康", "首次进入时读取")
        self.health_rank = RankList(rows=4, elide_width=140)
        health_card.add(self.health_rank, 1)

        parts, self.lbl_parts = _card_title("全部分区")
        self.part_rank = RankList(rows=5, elide_width=220)
        parts.add(self.part_rank, 1)
        root.addLayout(_two_col(health_card, parts), 3)

    def refresh_health(self) -> None:
        """读一次 SMART。开物理磁盘句柄比较慢，只在首次进入页面时跑一次。"""
        try:
            from core import diskhealth

            info = diskhealth.summary()
        except Exception:
            info = {}

        media = info.get("media") or "未知"
        smart = info.get("smart_ok")
        aligned = info.get("aligned")
        trim = info.get("trim_enabled")

        def mark(value):
            if value is True:
                return "正常", T.GREEN
            if value is False:
                return "异常", T.RED
            return "未知", T.TEXT_DIM

        rows = [
            ("介质类型", "", media, T.TEXT),
            ("健康状态", "", *mark(smart)),
            ("4K 对齐", "", *mark(aligned)),
            ("TRIM", "", *mark(trim)),
        ]
        self.health_rank.set_rows(rows)
        self.lbl_health.setText("SMART 只读")

    def update_metrics(self, snap: MetricsSnapshot) -> None:
        disk = snap.disk
        self.kpi_total.set_value(fmt_bytes(disk.total), disk.fs or "")
        self.kpi_used.set_value(
            fmt_bytes(disk.used), f"{disk.percent:.0f}%", T.load_color(disk.percent)
        )
        self.kpi_read.set_value(fmt_speed(disk.read_bps), "实时")
        self.kpi_write.set_value(fmt_speed(disk.write_bps), "实时")
        self.chart.push([disk.read_bps / 1024, disk.write_bps / 1024])
        self.lbl_mount.setText(f"{disk.mount} · {disk.fs}")

        self.part_rank.set_rows(
            [
                (
                    part["mount"],
                    part["fs"],
                    f"{part['percent']:.0f}% · 余 {fmt_bytes(part['free'])}",
                    T.load_color(part["percent"]),
                )
                for part in disk.partitions[:5]
            ]
        )
        total = sum(p["total"] for p in disk.partitions)
        self.lbl_parts.setText(
            f"{len(disk.partitions)} 个分区 · 共 {fmt_bytes(total)}" if total else ""
        )


class NetworkPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(9)

        self.kpi_down = KpiCard("下行", "", "network")
        self.kpi_up = KpiCard("上行", "", "network")
        self.kpi_recv = KpiCard("累计接收", "", "network")
        self.kpi_sent = KpiCard("累计发送", "", "network")
        root.addLayout(
            _kpi_row(
                [self.kpi_down, self.kpi_up, self.kpi_recv, self.kpi_sent]
            )
        )

        trend, _ = _card_title("网络吞吐", "KB/s · 最近 3 分钟")
        self.chart = TrendChart(
            [("下行", T.ACCENT), ("上行", T.CYAN)], capacity=180, unit=""
        )
        trend.add(self.chart, 1)
        root.addWidget(trend, 6)

        detail, self.lbl_ifaces = _card_title("网络接口")
        self.iface_rank = RankList(rows=5, elide_width=220)
        detail.add(self.iface_rank, 1)
        root.addWidget(detail, 3)

    def update_metrics(self, snap: MetricsSnapshot) -> None:
        net = snap.network
        self.kpi_down.set_value(fmt_speed(net.down_bps), "实时")
        self.kpi_up.set_value(fmt_speed(net.up_bps), "实时")
        self.kpi_recv.set_value(fmt_bytes(net.total_recv), "自开机")
        self.kpi_sent.set_value(fmt_bytes(net.total_sent), "自开机")
        self.chart.push([net.down_bps / 1024, net.up_bps / 1024])

        rows = []
        for item in net.interfaces[:5]:
            down = item.get("down_bps", 0.0)
            up = item.get("up_bps", 0.0)
            busy = down > 1024 or up > 1024
            rows.append(
                (
                    item["name"],
                    item.get("ip") or "—",
                    f"↓{fmt_speed(down)} ↑{fmt_speed(up)}" if busy else "空闲",
                    T.TEXT if busy else T.TEXT_DIM,
                )
            )
        self.iface_rank.set_rows(rows)

        online = [i for i in net.interfaces if i.get("up", True)]
        top = max((i.get("speed") or 0 for i in online), default=0)
        note = f"{len(online)} 个已连接"
        if top:
            note += f" · 最高 {top} Mbps"
        self.lbl_ifaces.setText(note)


class MonitorPage(QWidget):
    """监控入口：一个导航项，页内分段切换 6 个视图。

    以前这些页各自挂在主窗口的 QStackedWidget 上，侧栏要占 6 行。
    现在收进一个壳，数据分发和懒加载也一并下沉到这里：

    - 六个分段第一次切过去才建，建的时候用最近一帧数据补齐，不会空着。
    - update_metrics / update_processes 只转发给已经建出来的分段；它们各自
      维护趋势缓冲，切回来之前少算几帧没关系，切回去第一帧就接上了。
    - 磁盘 SMART 要开物理磁盘句柄，比较慢，只在第一次切到磁盘段时读一次。
    """

    segment_changed = Signal(str, str)
    kill_requested = Signal(int, str)
    kill_tree_requested = Signal(int, str)
    whitelist_requested = Signal(str)

    def __init__(self, store=None, on_navigate=None, parent=None):
        super().__init__(parent)
        self._store = store
        self._on_navigate = on_navigate
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(7)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self.seg = Segmented(SEGMENTS)
        head.addWidget(self.seg)
        head.addStretch(1)
        self.lbl_note = label("", 11, T.TEXT_DIM)
        head.addWidget(self.lbl_note, 0, Qt.AlignVCenter)
        root.addLayout(head)

        # 六个分段都是第一次切过去才建。进程表一个人就要占几 MB
        # （一屏四百多行的表格项），而多数人打开只看总览。
        self._pages: dict[str, QWidget] = {}
        self._last_snap: MetricsSnapshot | None = None
        self._last_proc: tuple | None = None
        # 内存段的状态字（「正在优化…」「释放 1.2 GB」）。内存段没建时先记着，
        # 建出来再补上，否则一键优化跑完切过去看不到结果。
        self._mem_status: tuple[str, str] | None = None

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self._key = SEGMENTS[0][0]
        self._prepared: set[str] = set()
        self.seg.changed.connect(self._on_segment)
        self._apply(self._key)

    # ---------------------------------------------------------- 分段

    def _ensure(self, key: str) -> QWidget | None:
        """取分段页面，没建过就现建一个，并用最近一帧数据填上。"""
        page = self._pages.get(key)
        if page is not None:
            return page

        if key == "overview":
            page = OverviewPage(self._store, self._on_navigate)
        elif key == "cpu":
            page = CpuPage()
        elif key == "memory":
            page = MemoryPage()
            if self._mem_status is not None:
                page.set_status(*self._mem_status)
        elif key == "disk":
            page = DiskPage()
        elif key == "network":
            page = NetworkPage()
        elif key == "process":
            page = ProcessPage()
            # 子页的信号对外还是走壳，主窗口不用知道它们藏在第几层
            page.kill_requested.connect(self.kill_requested.emit)
            page.kill_tree_requested.connect(self.kill_tree_requested.emit)
            page.whitelist_requested.connect(self.whitelist_requested.emit)
        else:
            return None

        self._pages[key] = page
        self.stack.addWidget(page)
        feed(page, self._last_snap, self._last_proc)
        return page

    def _apply(self, key: str) -> None:
        page = self._ensure(key)
        if page is None:
            return
        self._key = key
        self.seg.set_current(key, emit=False)
        self.stack.setCurrentWidget(page)
        self.lbl_note.setText(SEGMENT_NOTES.get(key, ""))
        self._prepare(key)

    def _on_segment(self, key: str) -> None:
        self._apply(key)
        self.segment_changed.emit(key, SEGMENT_SUBS.get(key, ""))

    def _prepare(self, key: str) -> None:
        """首次进入某段才做的慢操作。"""
        if key in self._prepared:
            return
        self._prepared.add(key)
        if key == "disk":
            page = self._pages.get("disk")
            if page is not None:
                QTimer.singleShot(0, page.refresh_health)

    def set_segment(self, key: str) -> None:
        """外部切段（换肤还原时用）。和用户点击走同一条路，副标题才会同步。"""
        if key in SEGMENT_KEYS:
            self._on_segment(key)

    def current_segment(self) -> str:
        return self._key

    def set_memory_status(self, text: str, color: str) -> None:
        """内存段左上角那行状态字。内存段没建就先记下，建的时候补上。"""
        self._mem_status = (text, color)
        page = self._pages.get("memory")
        if page is not None:
            page.set_status(text, color)

    def subtitle(self) -> str:
        return SEGMENT_SUBS.get(self._key, "")

    def start_scan(self) -> None:
        """F5：只有磁盘健康需要重读，其余分段是实时采样。"""
        page = self._pages.get("disk")
        if self._key == "disk" and page is not None:
            page.refresh_health()

    # ---------------------------------------------------------- 数据

    def update_metrics(self, snap: MetricsSnapshot) -> None:
        # 先存一帧：后面按需建出来的分段要立刻有数，不能等到下个采样周期
        self._last_snap = snap
        for page in self._pages.values():
            handler = getattr(page, "update_metrics", None)
            if handler is not None:
                handler(snap)

    def update_processes(self, rows: list[dict], total: int, leaks: set[int]) -> None:
        self._last_proc = (rows, total, leaks)
        for page in self._pages.values():
            handler = getattr(page, "update_processes", None)
            if handler is not None:
                handler(rows, total, leaks)

