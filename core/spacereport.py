"""系统盘「能回收什么」的汇总。

和 `cleaner` 的分工：清洁工负责**垃圾**（临时文件、缓存、日志，删了谁都不疼），
这里负责**大块占用**（休眠文件、还原点、组件存储、虚拟磁盘），每一项都对应一个
确切的动作，而且多数不能「删」，只能「关掉 / 迁走 / 让系统自己收缩」。

刻意不重复列清理页已有的项（更新缓存、传递优化、日志、社交缓存）：同一笔体积
在两个页面上算出不同的数，用户只会得出「你们自己都对不上」的结论。

分两批收集：
- `collect()` 只做 stat 和浅层聚合，一秒内出结果，够填满界面
- `collect_slow()` 要调 DISM / vssadmin，几十秒，放在「深入分析」按钮后面
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from . import dirstat, recycle
from .slim import _run as run_cmd

@dataclass
class Reclaim:
    """一项可回收的占用。

    `action` 决定界面上那颗按钮长什么样：
        clean   → 有清理入口，跳到 page
        open    → 只能打开系统界面让用户自己处理
        none    → 只统计，不给入口（删了会出事的那种）
    """

    key: str
    name: str
    size: int
    note: str
    action: str = "open"
    page: str = ""
    risk: str = ""
    admin: bool = False
    path: str = ""      # action=open 且指到目录时用

    @property
    def measurable(self) -> bool:
        return self.size > 0


# ------------------------------------------------------------------ 单元

_GB = 1024 ** 3


def _drive() -> str:
    return os.environ.get("SystemDrive", "C:") + "\\"


def _file_size(*names: str) -> int:
    total = 0
    for name in names:
        try:
            total += os.path.getsize(os.path.join(_drive(), name))
        except OSError:
            continue
    return total


def _dir_size(path: str, should_stop=lambda: False) -> int:
    """目录总体积。走 dirstat 的遍历器，比 os.walk + stat 快且不漏。"""
    if not os.path.isdir(path):
        return 0
    try:
        return dirstat.scan_subdir(path, should_stop=should_stop).size
    except OSError:
        return 0


def _local(name: str) -> str:
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), name)


# ------------------------------------------------------------------ 各项


def _hibernation() -> Reclaim | None:
    size = _file_size("hiberfil.sys")
    if not size:
        return None
    return Reclaim(
        "hibernation",
        "休眠文件",
        size,
        "和内存等量。关掉不能休眠，快速启动也失效（开机慢几秒）。",
        action="clean",
        page="slim",
        risk="关闭后无法休眠",
    )


def _pagefile() -> Reclaim | None:
    size = _file_size("pagefile.sys", "swapfile.sys")
    if not size:
        return None
    return Reclaim(
        "pagefile",
        "页面文件",
        size,
        "内存不够时用来换页。设成固定小值能省几 GB，但别关掉。",
        action="open",
        page="sysprops",
        risk="关闭会影响系统稳定性",
    )


def _dump() -> Reclaim | None:
    from .slim import _dump_size

    size = _dump_size()
    if not size:
        return None
    return Reclaim(
        "dump",
        "内存转储",
        size,
        "蓝屏时的内存快照，只用于排查崩溃，删了下次崩溃会重新生成。",
        action="clean",
        page="slim",
    )


def _windows_old(should_stop=lambda: False) -> Reclaim | None:
    from .slim import _old_size

    size = _old_size()
    if not size:
        return None
    return Reclaim(
        "windows_old",
        "旧系统文件",
        size,
        "升级留下的旧系统。删掉后不能再回退到上一版 Windows。",
        action="clean",
        page="slim",
        risk="删后无法回退系统版本",
    )


def _search_index() -> Reclaim | None:
    path = os.path.join(
        _drive(),
        "ProgramData", "Microsoft", "Search", "Data", "Applications",
        "Windows", "Windows.edb",
    )
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if not size:
        return None
    return Reclaim(
        "search_index",
        "搜索索引",
        size,
        "Windows 搜索的索引库。体积异常大时可在「索引选项」里重建，会重新爬一遍。",
        action="open",
        page="index",
    )


def _installer_cache(should_stop=lambda: False) -> Reclaim | None:
    size = _dir_size(os.path.join(_drive(), "Windows", "Installer"), should_stop)
    if not size:
        return None
    return Reclaim(
        "installer_cache",
        "安装缓存",
        size,
        "已装程序的卸载与修复数据。删掉后软件会「卸载失败」「无法修复」，不要手动清。",
        action="open",
        page="folder",
        risk="不要删除",
        path=os.path.join(_drive(), "Windows", "Installer"),
    )


def _store_apps(should_stop=lambda: False) -> Reclaim | None:
    size = _dir_size(os.path.join(_local("Packages")), should_stop)
    if not size:
        return None
    return Reclaim(
        "store_apps",
        "商店应用数据",
        size,
        "应用商店应用的数据。要清就在「设置 > 应用」里逐个重置或卸载，别删目录。",
        action="open",
        page="folder",
        path=os.path.join(_local("Packages")),
    )


def _virtual_disks(should_stop=lambda: False) -> Reclaim | None:
    """WSL / Docker 的 ext4.vhdx。这类文件只增不减，删了发行版就没了。"""
    total = 0
    detail = ""
    for base, pattern in (
        (os.path.join(_local("Packages")), "ext4.vhdx"),
        (os.path.join(_local("Docker")), ".vhdx"),
    ):
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base, onerror=lambda _e: None):
            if should_stop():
                break
            for name in filenames:
                if not name.lower().endswith(pattern):
                    continue
                try:
                    size = os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    continue
                total += size
                detail = dirpath
            # 只往下两层：Packages\<应用>\LocalState\
            if dirpath.count(os.sep) - base.count(os.sep) >= 2:
                dirnames[:] = []
    if not total:
        return None
    return Reclaim(
        "virtual_disk",
        "虚拟磁盘（WSL/Docker）",
        total,
        f"Linux 子系统的磁盘镜像，只会变大。用 wsl --manage 压缩，别直接删。{detail}",
        action="open",
        page="folder",
        risk="删除会丢 Linux 中的数据",
        path=detail,
    )


def _recycle_bin(should_stop=lambda: False) -> Reclaim | None:
    """回收站体积。

    优先用 `recycle.list_items()`（能报个数），但它在非管理员或跨用户 SID 的
    回收站上会返回空 —— 本机 `$Recycle.Bin` 明明躺着 180 MB，列表却是 0 条。
    所以再取一次目录体积，两者取大，至少不会把「有垃圾」说成「空的」。
    """
    try:
        items = recycle.list_items()
    except Exception:
        items = []
    size = sum(getattr(item, "size", 0) for item in items)
    if not size:
        size = _dir_size(os.path.join(_drive(), "$Recycle.Bin"), should_stop)
    if not size:
        return None
    if items:
        note = f"{len(items)} 个文件躺在回收站里，确认不需要就清空。"
    else:
        note = "回收站里还有东西，确认不需要就清空。"
    return Reclaim(
        "recycle",
        "回收站",
        size,
        note,
        action="clean",
        page="recycle",
    )


# ------------------------------------------------------------------ 慢项


def _restore_points() -> Reclaim | None:
    """系统还原点占用。走 vssadmin，需要管理员；输出随系统语言变，只抓数字。"""
    code, text = run_cmd(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32",
                                      "vssadmin.exe"), "list", "shadowstorage", timeout=25)
    if code != 0 or not text:
        return None
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(TB|GB|MB)", text, re.I)
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    unit = match.group(2).upper()
    size = int(value * {"TB": 1024 ** 4, "GB": _GB, "MB": 1024 ** 2}[unit])
    if size < 64 * 1024 ** 2:
        return None
    return Reclaim(
        "restore_points",
        "系统还原点",
        size,
        "系统保护保留的历史快照。可以只留最近一个，或在「系统保护」里调整配额。",
        action="open",
        page="restore",
        admin=True,
    )


def _component_store() -> Reclaim | None:
    """WinSxS 可回收量。DISM 要跑 30~60 秒，只在用户主动点「深入分析」时做。"""
    code, text = run_cmd(os.path.join(_drive(), "Windows", "System32", "Dism.exe"),
                         "/Online", "/Cleanup-Image", "/AnalyzeComponentStore",
                         timeout=300)
    if not text:
        return None
    patterns = (
        r"可回收.*?([\d.,]+)\s*(TB|GB|MB)",
        r"[Rr]eclaimable.*?([\d.,]+)\s*(TB|GB|MB)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.S)
        if match:
            value = float(match.group(1).replace(",", "."))
            unit = match.group(2).upper()
            size = int(value * {"TB": 1024 ** 4, "GB": _GB, "MB": 1024 ** 2}[unit])
            if size < 64 * 1024 ** 2:
                return None
            return Reclaim(
                "winsxs",
                "组件存储可回收",
                size,
                "更新替换下来的旧组件。清理后已安装的更新不能再卸载。",
                action="clean",
                page="slim",
                risk="清理需几分钟",
                admin=True,
            )
    return None


# ------------------------------------------------------------------ 入口

FAST_ITEMS = (
    _hibernation,
    _pagefile,
    _dump,
    _search_index,
)

# 要遍历目录的，比 stat 慢但通常 1-3 秒内
DIR_ITEMS = (
    _recycle_bin,
    _windows_old,
    _installer_cache,
    _store_apps,
    _virtual_disks,
)

SLOW_ITEMS = (_restore_points, _component_store)


def collect(
    include_dirs: bool = True,
    include_slow: bool = False,
    should_stop=lambda: False,
    on_item=None,
) -> list[Reclaim]:
    """收集可回收项。

    `on_item` 每算出一项就回调，界面可以边算边插，不用等整批。
    """
    items: list[tuple[object, tuple]] = [(fn, ()) for fn in FAST_ITEMS]
    if include_dirs:
        items += [(fn, (should_stop,)) for fn in DIR_ITEMS]
    if include_slow:
        items += [(fn, ()) for fn in SLOW_ITEMS]

    out: list[Reclaim] = []
    for fn, args in items:
        if should_stop():
            break
        try:
            item = fn(*args)
        except Exception as exc:  # 单项失败不该拖垮整张清单
            print(f"[space] {getattr(fn, '__name__', fn)} 失败: {exc}")
            item = None
        if item is None or not item.measurable:
            continue
        out.append(item)
        if on_item is not None:
            on_item(item)
    out.sort(key=lambda r: r.size, reverse=True)
    return out


def total(items: list[Reclaim]) -> int:
    return sum(item.size for item in items)


def split_note(items: list[Reclaim]) -> str:
    """一句话说明「能省多少、其中多少得动手」。"""
    from .winmem import fmt_size

    if not items:
        return ""
    auto = sum(item.size for item in items if item.action == "clean")
    return f"共 {fmt_size(total(items))}，其中 {fmt_size(auto)} 可一键回收"
