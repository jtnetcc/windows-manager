"""磁盘空间分析的入口：把「扫目录 + 收可回收项 + 记一笔历史」串成一次任务。

单独成文件是因为界面只该关心「什么时候有结果」，不该知道扫描器怎么遍历、
哪些项要调 DISM。和 `optimize.make_task` 一样的角色。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from . import dirstat, spacehistory, spacereport


@dataclass
class SpaceResult:
    root: dirstat.Node
    items: list[spacereport.Reclaim] = field(default_factory=list)
    used: int = 0
    total: int = 0
    seconds: float = 0.0
    single_disk: bool = False
    targets: list[str] = field(default_factory=list)
    scanned_at: float = 0.0
    cancelled: bool = False

    @property
    def ready(self) -> bool:
        return self.scanned_at > 0 and not self.cancelled

    @property
    def free(self) -> int:
        return max(0, self.total - self.used)

    @property
    def percent(self) -> float:
        return self.used / self.total * 100 if self.total else 0.0


def _usage(root: str) -> tuple[int, int]:
    try:
        import psutil

        usage = psutil.disk_usage(root)
        return usage.used, usage.total
    except Exception:
        return 0, 0


def usage() -> tuple[int, int]:
    """当前系统盘的 (已用, 总量)。

    单独开一个口子给界面：容量和已用在扫描一开始就该知道，否则堆叠条要等到
    扫完才有分母，中间那几十秒只能画一条空轨。
    """
    return _usage(os.environ.get("SystemDrive", "C:") + "\\")


def run(
    should_stop=lambda: False,
    report=lambda percent, text: None,
    on_dir=None,
    on_items=None,
    slow: bool = False,
) -> SpaceResult | None:
    """扫一次。返回 None 表示被取消。

    `on_dir(node, done, total)` 每扫完一个顶层目录回调一次 —— 界面靠它做
    「打开就有东西看」：先是 `C:\\Windows` 挂着「统计中」，扫完后定格成真实体积。

    `on_items(list)` 在开扫前先回一次可回收项（只含秒级能算的几项），让右侧
    「可回收」不用陪着目录一起等。
    """
    started = time.perf_counter()
    root_path = os.environ.get("SystemDrive", "C:") + "\\"
    layout = dirstat.Layout()

    # 容量先拿：堆叠条的分母、结论句的百分比都靠它
    used, total = _usage(root_path)

    if on_items is not None:
        try:
            on_items(spacereport.quick())
        except Exception as exc:  # 先摆的那几项失败了也不该拦住扫描
            print(f"[space] 预取可回收项失败: {exc}")

    report(3, "正在统计目录…")
    root = dirstat.scan(
        root_path,
        depth=dirstat.DEFAULT_DEPTH,
        workers=dirstat.workers_for(),
        should_stop=should_stop,
        on_dir_done=on_dir,
    )
    if should_stop():
        return None

    report(88, "正在统计可回收项…")
    items = spacereport.collect(
        include_dirs=True, include_slow=slow, should_stop=should_stop
    )
    if should_stop():
        return None

    spacehistory.record(root, used, total)
    report(100, "完成")

    return SpaceResult(
        root=root,
        items=items,
        used=used,
        total=total,
        seconds=time.perf_counter() - started,
        single_disk=layout.single,
        targets=layout.other_targets(),
        scanned_at=time.time(),
    )
