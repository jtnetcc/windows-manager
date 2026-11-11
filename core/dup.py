"""重复文件查找。

三段式，越往后越贵，所以只在必要时才算：

    1. 按体积分组      —— 只做一次 stat，体积不同的文件不可能重复
    2. 头部 4KB 哈希   —— 绝大多数「同体积但不同内容」在这一步就被排除
    3. 全量哈希        —— 只有前两步都撞上的才算真重复

直接用全量哈希遍历整个用户目录，动辄几分钟；三段式通常能省掉 95% 的读取量。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field

HEAD_BYTES = 4096
CHUNK = 1024 * 1024
PROGRESS_EVERY = 400

# 这些目录要么没有分析意义，要么会让遍历卡死
SKIP_DIR_NAMES = {
    "$recycle.bin",
    "system volume information",
    "windows",
    "winsxs",
    "node_modules",
    ".git",
    "__pycache__",
    ".venv",
    "venv",
}


@dataclass
class DupGroup:
    size: int
    files: list[str] = field(default_factory=list)

    @property
    def wasted(self) -> int:
        return self.size * max(0, len(self.files) - 1)

    @property
    def keeper(self) -> str:
        """保留哪一份。

        内容完全相同的文件留哪个都行，这里按「最早修改」保留，
        心理上更像原件。
        """
        return min(self.files, key=_mtime_or_zero)


def _mtime_or_zero(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _should_skip_dir(name: str) -> bool:
    return name.lower() in SKIP_DIR_NAMES


def _walk(roots: list[str], min_size: int, should_stop, on_progress):
    """第一段：按体积分组。"""
    by_size: dict[int, list[str]] = {}
    seen = 0
    total_bytes = 0

    for root in roots:
        if should_stop():
            return by_size
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _e: None):
            if should_stop():
                return by_size
            dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
            for name in filenames:
                if should_stop():
                    return by_size
                full = os.path.join(dirpath, name)
                try:
                    stat = os.stat(full)
                except OSError:
                    continue
                # seen 统计的是「走过了多少个文件」，不是候选数 ——
                # 否则界面上会显示「已扫描 30 个文件」其实已经走完几万个
                seen += 1
                total_bytes += stat.st_size
                if stat.st_size < min_size:
                    if seen % PROGRESS_EVERY == 0 and on_progress:
                        on_progress(seen, total_bytes, 0)
                    continue
                # 跳过符号链接 / 联接点，避免同一文件被算两次
                if getattr(stat, "st_nlink", 1) > 1 and os.path.islink(full):
                    continue
                by_size.setdefault(stat.st_size, []).append(full)
                if seen % PROGRESS_EVERY == 0 and on_progress:
                    on_progress(seen, total_bytes, 0)
    if on_progress:
        on_progress(seen, total_bytes, 0)
    return by_size


def _hash_file(path: str, limit: int | None = None) -> str | None:
    digest = hashlib.blake2b(digest_size=16)
    try:
        with open(path, "rb") as handle:
            remaining = limit
            while True:
                want = CHUNK if remaining is None else min(CHUNK, remaining)
                if want <= 0:
                    break
                chunk = handle.read(want)
                if not chunk:
                    break
                digest.update(chunk)
                if remaining is not None:
                    remaining -= len(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def scan(
    roots: list[str],
    min_size: int = 10 * 1024 * 1024,
    should_stop=lambda: False,
    on_progress=None,
):
    """扫描重复文件，逐组产出 `DupGroup`。"""
    by_size = _walk(roots, min_size, should_stop, on_progress)
    candidates = {size: paths for size, paths in by_size.items() if len(paths) > 1}
    if not candidates:
        return

    checked = 0
    group_count = 0
    for size, paths in sorted(candidates.items(), reverse=True):
        if should_stop():
            return
        # 第二段：头部哈希
        by_head: dict[str, list[str]] = {}
        for path in paths:
            if should_stop():
                return
            head = _hash_file(path, HEAD_BYTES)
            if head is None:
                continue
            by_head.setdefault(head, []).append(path)
        same_head = [group for group in by_head.values() if len(group) > 1]
        if not same_head:
            continue

        # 第三段：全量哈希
        for group in same_head:
            if should_stop():
                return
            by_full: dict[str, list[str]] = {}
            for path in group:
                if should_stop():
                    return
                full = _hash_file(path)
                checked += 1
                if full is None:
                    continue
                by_full.setdefault(full, []).append(path)
            for files in by_full.values():
                if len(files) > 1:
                    group_count += 1
                    yield DupGroup(size=size, files=sorted(files))
                    if on_progress:
                        on_progress(0, 0, group_count)


def collect(roots: list[str], min_size: int = 10 * 1024 * 1024,
            should_stop=lambda: False, on_progress=None) -> list[DupGroup]:
    return list(scan(roots, min_size, should_stop, on_progress))


def total_wasted(groups: list[DupGroup]) -> int:
    return sum(g.wasted for g in groups)


def cleanup_list(groups: list[DupGroup], keep_one: bool = True) -> list[str]:
    """生成待删除清单：每组留一份，其余都删。"""
    victims: list[str] = []
    for group in groups:
        if keep_one:
            keeper = group.keeper
            victims.extend(p for p in group.files if p != keeper)
        else:
            victims.extend(group.files)
    return victims
