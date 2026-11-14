"""系统盘空间分析：一次遍历算出每个目录占多少。

和 `bigfile` 的区别：那边回答「哪几个文件大」，这边回答「空间去哪了」。
单分区机器上后者才是真问题 —— 出事的往往是「每个文件都不大、整个目录几十 GB」
的地方（`Windows\\Installer`、`ProgramData\\Package Cache`、`AppData\\Local\\Packages`）。

几个实现决定，都有实测依据（见 docs/C盘扫描加强方案.md）：

- 用 `os.scandir` + `entry.stat()`，不用 `os.walk` + `os.stat`。同样扫 C 盘，
  前者 40 s 拿到 39.4 万个文件 / 44.6 GB，后者 48 s 只有 35.3 万个 / 34.6 GB。
  差别在权限：`os.stat` 走 GetFileAttributesEx，受保护文件直接拒绝访问；
  目录枚举已经把大小一起返回了，用这份数据就不会漏。
- 目录体积用递归返回值逐层相加，不做「事后按路径深度合并」。后者试过，
  `C:\\Users` 9.81 GB 会被算成 0.29 GB。
- 跳过 reparse point，否则 `C:\\Users\\All Users`、`C:\\Documents and Settings`
  会带着扫描器绕圈。
- 云端占位文件（OneDrive「按需」）单独计数，不计入体积 —— 它们的逻辑大小几十 GB，
  本地实际是 0。
"""

from __future__ import annotations

import os
import stat as statmod
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

REPARSE_POINT = 0x400
# 云文件占位：本地只有几十字节的重解析信息，真身还在服务器上
CLOUD_ATTRS = 0x00400000 | 0x00040000 | 0x00100000

SKIP_DIRS = {
    "system volume information",
    "node_modules",
    ".git",
    "__pycache__",
    ".venv",
    "venv",
}

DEFAULT_DEPTH = 2
# 小于这个体积的子目录并进「其他 N 项」：列出来也只是噪音
MIN_NODE_BYTES = 8 << 20
# 只对这个体积以上的文件做硬链接去重。WinSxS 里有几万个硬链接，
# 但那里本来就跳过；把 39 万个 inode 全记下来要多花 40 MB 内存，不值。
INODE_MIN_BYTES = 1 << 20
PROGRESS_EVERY = 800


@dataclass
class Node:
    """一个目录（或文件）的占用。size 含全部后代。"""

    path: str
    size: int = 0
    files: int = 0
    errors: int = 0            # 无权限读不了的条目数
    cloud: int = 0             # 云端占位文件的逻辑大小（不计入 size）
    cloud_files: int = 0
    is_dir: bool = True
    children: list["Node"] = field(default_factory=list)
    folded: int = 0            # 被并进「其他」的子项个数
    folded_size: int = 0
    title: str = ""            # 覆盖显示名（根目录聚合项用）
    kind: str = ""             # 覆盖分类，空表示按路径判断

    @property
    def name(self) -> str:
        if self.title:
            return self.title
        base = os.path.basename(self.path.rstrip("\\"))
        return base or self.path

    def child(self, name: str) -> "Node | None":
        low = name.lower()
        for item in self.children:
            if item.name.lower() == low:
                return item
        return None


class _Ctx:
    """一条遍历线自己的计数器和 inode 表。

    每个顶层子树一份，所以不需要加锁 —— 跨顶层目录的硬链接极少，
    为此上全局锁会让整次扫描慢 20% 以上。
    """

    __slots__ = ("seen", "files", "current", "stop")

    def __init__(self, stop):
        self.seen: set[tuple[int, int]] = set()
        self.files = 0
        self.current = ""
        self.stop = stop


def _scan_dir(path: str, level: int, depth: int, ctx: _Ctx) -> Node:
    node = Node(path=path)
    try:
        it = os.scandir(path)
    except OSError:
        node.errors += 1
        return node

    keep_children = level < depth
    with it:
        for entry in it:
            if ctx.stop():
                return node
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                node.errors += 1
                continue

            if statmod.S_ISDIR(st.st_mode):
                if st.st_file_attributes & REPARSE_POINT:
                    continue
                if entry.name.lower() in SKIP_DIRS:
                    continue
                child = _scan_dir(entry.path, level + 1, depth, ctx)
                node.size += child.size
                node.files += child.files
                node.errors += child.errors
                node.cloud += child.cloud
                node.cloud_files += child.cloud_files
                # 顶层永远全留（就那么十几个），深一层按体积过滤
                if keep_children and (level == 0 or child.size >= MIN_NODE_BYTES):
                    node.children.append(child)
                else:
                    node.folded += 1
                    node.folded_size += child.size
                continue

            size = st.st_size
            if not size:
                continue
            if st.st_file_attributes & CLOUD_ATTRS:
                node.cloud += size
                node.cloud_files += 1
                continue
            if size >= INODE_MIN_BYTES and st.st_ino:
                key = (st.st_dev, st.st_ino)
                if key in ctx.seen:
                    continue
                ctx.seen.add(key)
            node.size += size
            node.files += 1
            ctx.files += 1

    node.children.sort(key=lambda n: n.size, reverse=True)
    return node


def _scan_root(root: str, depth: int, stop, on_progress, on_dir_done,
               workers: int) -> Node:
    """扫一层：root 的直接子项并行处理，谁先完成谁先回调。"""
    total = Node(path=root)
    try:
        entries = list(os.scandir(root))
    except OSError:
        total.errors += 1
        return total

    dirs: list[str] = []
    for entry in entries:
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            total.errors += 1
            continue
        if statmod.S_ISDIR(st.st_mode):
            if st.st_file_attributes & REPARSE_POINT:
                continue
            if entry.name.lower() in SKIP_DIRS:
                continue
            dirs.append(entry.path)
            continue
        size = st.st_size
        if not size:
            continue
        if st.st_file_attributes & CLOUD_ATTRS:
            total.cloud += size
            total.cloud_files += 1
            continue
        # 根目录散落的文件单独成一项：单分区用户往 C:\ 上扔东西很常见
        total.size += size
        total.files += 1
        total.children.append(
            Node(path=entry.path, size=size, files=1, is_dir=False)
        )

    if not dirs:
        total.children.sort(key=lambda n: n.size, reverse=True)
        return total

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(_scan_dir, path, 1, depth, _Ctx(stop)): path
            for path in dirs
        }
        for future in as_completed(futures):
            node = future.result()
            total.size += node.size
            total.files += node.files
            total.errors += node.errors
            total.cloud += node.cloud
            total.cloud_files += node.cloud_files
            total.children.append(node)
            done += 1
            if on_dir_done is not None:
                on_dir_done(node, done, len(dirs))
            if on_progress is not None:
                on_progress(total, done, len(dirs))

    _bundle_loose_files(root, total)
    total.children.sort(key=lambda n: n.size, reverse=True)
    return total


def _bundle_loose_files(root: str, total: Node) -> None:
    """把根目录下散落的文件并成一行。

    本机 `C://` 上躺着 hiberfil.sys / pagefile.sys / swapfile.sys / DumpStack.log.tmp
    四个文件，不合并就占掉列表四行，把真正的目录挤到下面去 —— 而目录才是
    这一页要回答的问题。合并项自己带着 children，点开还是能看到具体是哪些文件。
    """
    loose = [n for n in total.children if not n.is_dir]
    if len(loose) < 2:
        return
    loose.sort(key=lambda n: n.size, reverse=True)
    bundle = Node(
        path=root,
        title="（根目录散落文件）",
        is_dir=True,
        size=sum(n.size for n in loose),
        files=sum(n.files for n in loose),
        children=loose,
        kind=category_of(loose[0]),
    )
    total.children = [n for n in total.children if n.is_dir] + [bundle]


def scan(
    root: str | None = None,
    depth: int = DEFAULT_DEPTH,
    workers: int = 4,
    should_stop=lambda: False,
    on_progress=None,
    on_dir_done=None,
) -> Node:
    """扫一个根目录。

    `depth=2` 表示「根 + 一层子目录」，界面上正好是一屏能看懂的粒度；
    再深的内容在用户点进去时按需再扫，比缓存整棵树省内存得多。

    `on_dir_done(node, 完成数, 总数)` 每扫完一个顶层子树回调一次 —— 界面靠它
    做到「2 秒后就有东西看」，而不是等全盘 40 秒才出第一行。
    """
    root = root or os.environ.get("SystemDrive", "C:") + "\\"
    return _scan_root(root, depth, should_stop, on_progress, on_dir_done, workers)


def scan_subdir(path: str, workers: int = 4, should_stop=lambda: False) -> Node:
    """下钻用：只列这个目录的直接子项，几百毫秒级。"""
    node = _scan_root(path, 1, should_stop, None, None, workers)
    node.is_dir = True
    return node


def category_of(node: Node) -> str:
    """把顶层目录归到「系统 / 应用 / 用户 / 系统文件」四类，给堆叠条用。

    靠路径名判断看着土，但这是唯一不依赖额外探测的办法，而且分类错了
    只影响一根条的颜色，不影响任何数字。
    """
    if node.kind:
        return node.kind
    path = node.path.lower().rstrip("\\") + "\\"
    drive = (os.environ.get("SystemDrive", "C:") + "\\").lower()

    if path.startswith(drive + "windows\\") or path.startswith(drive + "windows."):
        return "system"
    if path.startswith(drive + "program files\\") or path.startswith(
        drive + "program files (x86)\\"
    ):
        return "app"
    if path.startswith(drive + "programdata\\"):
        return "app"
    if path.startswith(drive + "users\\"):
        return "user"
    if path.startswith(drive + "$recycle.bin\\") or path.startswith(
        drive + "recovery\\"
    ):
        return "system"
    if path.startswith(drive + "system volume information\\"):
        return "system"
    if not node.is_dir:
        # 根目录散落的文件里，休眠/分页文件算系统，其余算其他
        name = node.name.lower()
        if name in ("hiberfil.sys", "pagefile.sys", "swapfile.sys", "memory.dmp"):
            return "system_file"
        return "other"
    return "other"


CATEGORY_LABEL = {
    "system": "系统",
    "app": "应用",
    "user": "用户",
    "system_file": "系统文件",
    "other": "其他",
}


def summarize(root: Node, used: int) -> dict[str, int]:
    """按类别汇总。`used` 是磁盘实际已用量，用来兜住「扫不到的那部分」。

    扫不到的部分（`System Volume Information`、被独占的文件）会让五段加起来
    比系统报的已用少一截。不兜的话堆叠条永远填不满，看起来像少了几个 GB。
    """
    out = {"system": 0, "app": 0, "user": 0, "system_file": 0, "other": 0}
    for node in root.children:
        out[category_of(node)] += node.size
    scanned = sum(out.values())
    if used > scanned:
        out["other"] += used - scanned
    return out


def describe(node: Node) -> str:
    """一行摘要：多少 GB、多少文件、有几项读不了。"""
    from .winmem import fmt_size

    text = f"{fmt_size(node.size)} · {node.files:,} 个文件"
    if node.cloud:
        text += f" · 另有 {fmt_size(node.cloud)} 在云端"
    if node.errors:
        text += f" · {node.errors} 项无权限"
    return text


class Layout:
    """分区布局。单分区机型要走另一套建议 —— 没有「搬到别的盘」这条路。"""

    def __init__(self) -> None:
        self.fixed: list[str] = []
        self.removable: list[str] = []
        self.system_drive = os.environ.get("SystemDrive", "C:") + "\\"
        self.refresh()

    def refresh(self) -> None:
        try:
            import psutil

            self.fixed, self.removable = [], []
            for part in psutil.disk_partitions(all=False):
                if not part.fstype:
                    continue
                if "removable" in part.opts:
                    self.removable.append(part.mountpoint)
                else:
                    self.fixed.append(part.mountpoint)
        except Exception:
            self.fixed, self.removable = [self.system_drive], []

    @property
    def single(self) -> bool:
        return len(self.fixed) <= 1

    def other_targets(self) -> list[str]:
        return [p for p in self.fixed if p.rstrip("\\") != self.system_drive.rstrip("\\")] + \
            self.removable


def workers_for() -> int:
    """并行度：SSD 上 4 路能提速三成，机械盘并行只会让磁头来回跑。"""
    try:
        from . import diskhealth

        media = diskhealth.summary().get("media")
    except Exception:
        media = "Unknown"
    return 4 if media == "SSD" else 1
