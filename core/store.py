"""应用级数据仓库：所有扫描结果的唯一来源。

之前每个页面自己扫、自己存，导致两个实际问题：

1. 清理页刚扫过的数据，体检页要**再扫一遍**才能用
2. 同一份数据在不同页面显示的数字不一样（扫描时间不同）

现在统一走这里：任务把结果写进来，页面只订阅信号、不再持有业务数据。
`apply()` 是唯一的写入口，task_id 决定结果落到哪个字段。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal


@dataclass
class JunkState:
    items: list = field(default_factory=list)
    total: int = 0
    update_backlog: int = 0
    # 其中属于社交软件缓存的部分，体检要单独算一项
    social: int = 0
    scanned_at: float = 0.0

    @property
    def ready(self) -> bool:
        return self.scanned_at > 0


@dataclass
class DupState:
    groups: list = field(default_factory=list)
    wasted: int = 0
    scanned_at: float = 0.0

    @property
    def ready(self) -> bool:
        return self.scanned_at > 0


@dataclass
class BigFileState:
    files: list = field(default_factory=list)
    total: int = 0
    scanned_at: float = 0.0
    # 这次扫描用的条件。换肤会重建页面，靠它把「按视频扫的」恢复回来，
    # 否则重建后空列表会被说成「没有超过阈值的大文件」。
    kinds: list = field(default_factory=list)
    min_size: int = 0
    truncated: bool = False

    @property
    def ready(self) -> bool:
        return self.scanned_at > 0


@dataclass
class SpaceState:
    """磁盘空间分析结果。换肤重建页面时靠它把结果还原回来，
    不然切个主题整页就空了，用户得再扫两分钟。"""

    result: object = None
    scanned_at: float = 0.0

    @property
    def ready(self) -> bool:
        return self.result is not None and self.scanned_at > 0


@dataclass
class StartupState:
    entries: list = field(default_factory=list)
    scanned_at: float = 0.0

    @property
    def enabled(self) -> int:
        return sum(1 for e in self.entries if getattr(e, "enabled", False))

    @property
    def ready(self) -> bool:
        return self.scanned_at > 0


@dataclass
class RegistryState:
    issues: list = field(default_factory=list)
    scanned_at: float = 0.0

    @property
    def ready(self) -> bool:
        return self.scanned_at > 0


@dataclass
class SoftwareState:
    apps: list = field(default_factory=list)
    scanned_at: float = 0.0

    @property
    def ready(self) -> bool:
        return self.scanned_at > 0


class Store(QObject):
    """数据仓库。所有信号都在主线程发，页面可以直接改控件。"""

    junk_changed = Signal()
    dup_changed = Signal()
    bigfile_changed = Signal()
    startup_changed = Signal()
    software_changed = Signal()
    registry_changed = Signal()
    space_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.junk = JunkState()
        self.dup = DupState()
        self.bigfile = BigFileState()
        self.startup = StartupState()
        self.software = SoftwareState()
        self.registry = RegistryState()
        self.space = SpaceState()

    # ---------------------------------------------------------- 写入

    def apply(self, task_id: str, result) -> bool:
        """把任务结果落到对应字段，返回是否真的更新了。

        result 为 None 表示任务被取消或失败，调用方不应该动数据。
        """
        if result is None:
            return False

        if task_id == "junk":
            self.junk = result
            self.junk_changed.emit()
        elif task_id == "dup":
            self.dup = result
            self.dup_changed.emit()
        elif task_id == "bigfile":
            self.bigfile = result
            self.bigfile_changed.emit()
        elif task_id == "startup":
            # 启动项与计划任务已合并成一页，共用这一份状态
            self.startup = result
            self.startup_changed.emit()
        elif task_id == "software":
            self.software = result
            self.software_changed.emit()
        elif task_id == "registry":
            self.registry = result
            self.registry_changed.emit()
        elif task_id == "space":
            self.space = SpaceState(result=result, scanned_at=result.scanned_at)
            self.space_changed.emit()
        else:
            return False
        return True

    def set_junk_items(
        self, items: list, total: int, update_backlog: int,
        social: int | None = None,
    ) -> None:
        """清理动作执行后，就地更新剩余项，不用重扫。

        social 不单独传时按剩余项重算：漏掉它会让体检误以为社交缓存已经清空。
        """
        if social is None:
            social = sum(
                i.size for i in items if getattr(i, "category", "") == "社交缓存"
            )
        self.junk = JunkState(
            items=items,
            total=total,
            update_backlog=update_backlog,
            social=social,
            scanned_at=time.time(),
        )
        self.junk_changed.emit()

    def set_dup_groups(self, groups: list) -> None:
        """重复文件清理后就地更新，不用重扫。

        不写回的话有两个后果：顶部「可释放」一直挂着一笔早就不存在的体积，
        而且从别的页面回来（或换肤重建）列表里又会冒出来已经送进回收站的文件。
        """
        self.dup = DupState(
            groups=groups,
            wasted=sum(getattr(g, "wasted", 0) for g in groups),
            scanned_at=time.time(),
        )
        self.dup_changed.emit()

    def set_bigfile_files(self, files: list) -> None:
        """删除/清理后就地更新大文件列表，不用再扫一遍整盘。

        扫描条件（类型/阈值）沿用上一次，删完再刷新时不能说成「全部类型」。
        """
        old = self.bigfile
        self.bigfile = BigFileState(
            files=files,
            total=sum(f.size for f in files),
            scanned_at=time.time(),
            kinds=list(old.kinds),
            min_size=old.min_size,
            truncated=False,
        )
        self.bigfile_changed.emit()

    # ---------------------------------------------------------- 查询

    def health_inputs(self) -> dict:
        """给体检用的输入。没扫过的项不传，体检那边会标成「未测」。"""
        out: dict = {}
        if self.junk.ready:
            out["junk_bytes"] = self.junk.total
            out["update_backlog"] = self.junk.update_backlog
            out["social_bytes"] = self.junk.social
        if self.dup.ready:
            out["dup_bytes"] = self.dup.wasted
        if self.registry.ready:
            out["registry_count"] = len(self.registry.issues)
        return out

    def reclaimable_parts(self) -> list[tuple[str, str, int, float]]:
        """顶部「可释放 X」的明细：(名称, 目标页, 体积, 扫描时间)。

        这个数字是「垃圾 + 重复文件」相加，但两者要在不同页面处理。原来点一下
        无脑跳垃圾清理页，遇到「4.9 GB 全是重复文件」就会看到一个几乎空的
        清理页 —— 用户只能得出「这数字清不掉」的结论。
        """
        parts: list[tuple[str, str, int, float]] = []
        if self.junk.ready and self.junk.total > 0:
            parts.append(("垃圾文件", "clean", self.junk.total, self.junk.scanned_at))
        if self.dup.ready and self.dup.wasted > 0:
            parts.append(("重复文件", "dup", self.dup.wasted, self.dup.scanned_at))
        return parts
