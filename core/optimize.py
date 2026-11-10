"""一键优化：把低风险动作串成一条链路。

之前的「一键优化」只整理了内存工作集，但按钮叫这个名字，用户期待的是
「把系统弄干净」。现在按真实链路来：

1. 扫描垃圾（store 里有就直接复用，不重复扫）
2. 清理垃圾
3. 整理内存工作集
4. 汇总建议（重复文件、启动项）——**只给建议，不自动执行**

第 4 步故意不自动做：重复文件删错了找不回来，启动项禁用可能让用户开机
少个常用软件。这两项留给用户自己确认，一键优化只负责「报告有什么可做」。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import cleaner, optimizer, winmem


@dataclass
class Step:
    name: str
    freed: int = 0
    detail: str = ""
    ok: bool = True


@dataclass
class Report:
    steps: list[Step] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    duration: float = 0.0

    @property
    def freed(self) -> int:
        return sum(s.freed for s in self.steps)

    def summary(self) -> str:
        if self.freed <= 0:
            return "没有可释放的空间"
        return f"释放 {winmem.fmt_size(self.freed)}"


def make_task(store, deep: bool = False):
    """构造任务函数，交给任务中心执行。

    任务函数签名约定：`fn(report, should_stop) -> Report | None`
    """

    def run(report, should_stop):
        started = time.perf_counter()
        out = Report()

        # ---- 1. 垃圾：store 里有就复用，没有就现扫
        items = list(store.junk.items) if store.junk.ready else []
        if not items:
            report(5, "正在扫描垃圾文件…")
            scanned: list[cleaner.FileItem] = []
            # 没管理员权限时不要碰需要提权的规则：扫出来也删不掉，
            # 只会让用户看到一堆「被占用」
            allowed = [
                rule.id
                for rule in cleaner.builtin_rules()
                if not rule.needs_admin or winmem.is_admin()
            ]
            for batch in cleaner.scan(
                allowed,
                should_stop=should_stop,
                on_progress=lambda _rid, n: report(
                    5, f"正在扫描垃圾文件… 已找到 {len(scanned) + n} 个"
                ),
            ):
                scanned.extend(batch)
                if should_stop():
                    return None
            items = scanned

        if should_stop():
            return None

        # ---- 2. 清理垃圾
        if items:
            report(45, f"正在清理 {len(items)} 个文件…")
            deleted, failed, freed, remaining = cleaner.delete_items(items)
            detail = f"{deleted} 个文件"
            if failed:
                detail += f"，{failed} 个被占用已跳过"
            out.steps.append(Step("清理垃圾文件", freed, detail))
            # 没删掉的写回 store：清理页和概览上的体积要跟着变成真实值，
            # 直接清零会让用户以为垃圾都清干净了
            store.set_junk_items(
                remaining,
                sum(i.size for i in remaining),
                cleaner.update_backlog(remaining),
            )
        else:
            out.steps.append(Step("清理垃圾文件", 0, "没有可清理的文件"))

        if should_stop():
            return None

        # ---- 3. 整理内存
        report(70, "正在整理内存…")
        try:
            result = optimizer.optimize(deep)
            detail = (
                f"释放 {winmem.fmt_size(result.freed)}" if result.freed else "无需整理"
            )
            out.steps.append(Step("整理内存工作集", result.freed, detail))
        except Exception as exc:
            out.steps.append(Step("整理内存工作集", 0, f"跳过：{exc}", ok=False))

        # ---- 4. 只给建议，不自动执行
        report(92, "正在汇总建议…")
        if store.dup.ready and store.dup.wasted > 0:
            out.suggestions.append(
                f"有 {winmem.fmt_size(store.dup.wasted)} 重复文件可清理（去「重复文件」页确认）"
            )
        if store.startup.ready and store.startup.enabled > 8:
            out.suggestions.append(
                f"开机启动项 {store.startup.enabled} 个，建议精简（去「启动项」页）"
            )
        try:
            from . import trash

            size = trash.total_size()
            if size > 1024 ** 3:
                out.suggestions.append(
                    f"回收站占用 {winmem.fmt_size(size)}，可以清空（去「垃圾清理」页）"
                )
        except Exception:
            pass

        out.duration = time.perf_counter() - started
        report(100, "完成")
        return out

    return run
