"""大文件分析：找出占空间最多的文件，并按格式归类。

设计取舍：
- 只列清单，不做花哨的可视化；
- 默认阈值 200MB，小于这个数清一堆也腾不出空间，反而让列表没法看；
- 删除一律走回收站（`trash.recycle`），不做物理删除 —— 用户手滑了还能还原。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import safety, trash, winpath
from .dup import SKIP_DIR_NAMES

# FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS / ON_OPEN / OFFLINE
_CLOUD_ATTRS = 0x00400000 | 0x00040000 | 0x00100000

# 系统盘根目录下的这几个文件：扫得出来，删不得。
# 注意不能直接用 `safety.is_protected` —— 它把整个 Program Files 和 Windows
# 都算「受保护」，拿它当「不可勾选」会让系统盘范围扫出来的每一行都变灰。
_SYSTEM_FILE_NAMES = {"hiberfil.sys", "pagefile.sys", "swapfile.sys", "memory.dmp", "bootmgr"}


def is_system_file(path: str) -> bool:
    return os.path.basename(path).lower() in _SYSTEM_FILE_NAMES

PROGRESS_EVERY = 500
DELETE_BATCH = 100

# 类型归类。顺序就是界面筛选条的排列顺序。
CATEGORIES: list[tuple[str, str]] = [
    ("video", "视频"),
    ("image", "图片"),
    ("audio", "音频"),
    ("archive", "压缩包"),
    ("installer", "安装包"),
    ("document", "文档"),
    ("other", "其他"),
]
CATEGORY_LABEL = dict(CATEGORIES)

_EXT_MAP: dict[str, str] = {}
for _cat, _exts in {
    "video": (
        "mp4 mkv avi mov wmv flv rmvb rm ts m4v webm mpg mpeg 3gp vob m2ts mts "
        "asf f4v divx ogv m2v mxf wtv dvr-ms"
    ),
    "image": (
        "jpg jpeg png gif bmp webp tif tiff heic heif avif jxl raw cr2 nef arw "
        "dng psd ai svg eps"
    ),
    "audio": "mp3 flac wav aac m4a ogg wma ape aiff aif mid midi opus dsf dff amr",
    "archive": "zip rar 7z tar gz bz2 xz cab lz4 zst tgz tbz2 txz npk wpk pak vpk",
    "installer": (
        "exe msi msix msixbundle appx appxbundle msu iso img vhd vhdx wim dmg "
        "pkg deb rpm apk xapk"
    ),
    "document": (
        "pdf doc docx xls xlsx ppt pptx txt md csv epub mobi azw3 chm xps rtf "
        "odt ods odp wps et dps"
    ),
}.items():
    for _ext in _exts.split():
        _EXT_MAP[_ext] = _cat


def extensions_for(kind: str) -> list[str]:
    """某一类支持的扩展名（界面 tooltip 用，避免文案和规则脱节）。"""
    return sorted(ext for ext, cat in _EXT_MAP.items() if cat == kind)


# `.exe` 的特殊处理：目录里 99% 的 exe 是程序本体，不是安装包。
# 只扫「安装包」时按文件名认一下，不然列表里全是 WorkBuddy.exe、node.exe 这种，
# 用户一不小心就把正在用的软件删了。
_INSTALLER_WORDS = (
    "setup", "install", "安装", "更新", "update", "patch", "补丁", "upgrade", "升级",
)


def _looks_installer(name: str) -> bool:
    low = name.lower()
    if "uninstall" in low or "unins" in low:  # 卸载器，不是安装包
        return False
    return any(word in low for word in _INSTALLER_WORDS)


# 按类型扫描时的默认体积门槛（MB）。
# 视频/安装包这类目标本身就不大，用统一的 200MB 会把绝大多数筛掉，
# 所以切类型时把门槛一起调低 —— 用户仍可手动改。
KIND_MIN_MB: dict[str, int] = {
    "all": 200,
    "video": 30,
    "image": 5,
    "audio": 5,
    "archive": 10,
    "installer": 5,
    "document": 5,
    "other": 50,
}


def suggest_min_mb(kind: str) -> int:
    return KIND_MIN_MB.get(kind, 50)


@dataclass
class BigFile:
    path: str
    size: int
    mtime: float
    # 系统文件（休眠、分页、引导）。扫得出来但删不得，界面上要标灰不可勾，
    # 否则用户勾了才发现「失败」，还以为程序有 bug。
    locked: bool = False

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def folder(self) -> str:
        return os.path.dirname(self.path)

    @property
    def ext(self) -> str:
        return os.path.splitext(self.path)[1].lstrip(".").lower()

    @property
    def category(self) -> str:
        return _EXT_MAP.get(self.ext, "other")

    @property
    def category_label(self) -> str:
        return CATEGORY_LABEL.get(self.category, "其他")


# ------------------------------------------------------------------ 扫描范围

PRESETS: list[tuple[str, str]] = [
    # 单分区机器上「用户目录」只覆盖 C://Users//xxx，装在 C://Games、
    # C://Program Files 的东西全看不见，所以整个系统盘放第一位并做默认。
    ("system", "系统盘"),
    ("home", "用户目录"),
    ("downloads", "下载文件夹"),
    ("desktop", "桌面"),
    ("media", "视频与图片"),
    ("drives", "整个磁盘"),
    ("custom", "选择目录…"),
]


def _user_dirs(*names: str) -> list[str]:
    home = Path.home()
    found = []
    for name in names:
        path = home / name
        if path.is_dir():
            found.append(str(path))
    return found


def preset_roots(key: str) -> list[str]:
    """把界面上的「扫描范围」翻译成目录列表。"""
    if key == "system":
        return [os.environ.get("SystemDrive", "C:") + "\\"]
    if key == "home":
        return winpath.home_roots()
    if key == "downloads":
        return _user_dirs("Downloads", "下载")
    if key == "desktop":
        return _user_dirs("Desktop", "桌面")
    if key == "media":
        return _user_dirs("Videos", "视频", "Pictures", "图片", "Music", "音乐")
    if key == "drives":
        return winpath.fixed_drives()
    return []


# ------------------------------------------------------------------ 扫描


@dataclass
class ScanResult:
    """一次扫描的结果。除了清单，还带上「为什么没显示全」的依据。"""

    files: list[BigFile]
    seen: int = 0          # 走过的文件总数（含不符合类型的）
    matched: int = 0       # 符合条件、但可能被 limit 截掉的个数
    truncated: bool = False
    cloud_skipped: int = 0   # 因为「只在云端」被跳过的文件数


def scan_ex(
    roots: list[str],
    min_size: int = 200 * 1024 * 1024,
    limit: int = 800,
    kinds: list[str] | None = None,
    should_stop=lambda: False,
    on_progress=None,
) -> ScanResult:
    """扫描。

    `kinds` 为空表示不限类型；给了类型就**先按扩展名筛，再 stat** ——
    顺序反过来的话，全盘扫描要给每个文件都取一次属性，慢得没道理。
    """
    want = set(kinds) if kinds else None
    only_other = want == {"other"}
    installer_only = want == {"installer"}
    # 只选了「其他」时无法按扩展名白名单预筛，只能 stat 之后再判
    allowed: set[str] | None = None
    if want and not only_other:
        allowed = {ext for ext, cat in _EXT_MAP.items() if cat in want}
        if installer_only:
            allowed.discard("exe")  # exe 要按文件名判，见下面的分支

    found: list[BigFile] = []
    seen = 0
    cloud = 0

    for root in roots:
        if should_stop():
            break
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _e: None):
            if should_stop():
                break
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIR_NAMES]
            for name in filenames:
                seen += 1
                ext = os.path.splitext(name)[1].lstrip(".").lower()
                if installer_only and ext == "exe":
                    if not _looks_installer(name):
                        continue
                elif allowed is not None and ext not in allowed:
                    continue
                if only_other and _EXT_MAP.get(ext, "other") != "other":
                    continue
                full = os.path.join(dirpath, name)
                try:
                    stat = os.stat(full)
                except OSError:
                    continue
                if stat.st_size < min_size:
                    continue
                # OneDrive「按需」文件的逻辑大小几十 GB、本地实际 0 字节，
                # 列进大文件清单就是纯误导
                if stat.st_file_attributes & _CLOUD_ATTRS:
                    cloud += 1
                    continue
                found.append(BigFile(full, stat.st_size, stat.st_mtime))
                if seen % PROGRESS_EVERY == 0 and on_progress:
                    on_progress(seen, len(found))

    if on_progress:
        on_progress(seen, len(found))

    found.sort(key=lambda f: f.size, reverse=True)
    top = found[:limit]
    for item in top:
        item.locked = is_system_file(item.path)
    return ScanResult(
        files=top,
        seen=seen,
        matched=len(found),
        truncated=len(found) > limit,
        cloud_skipped=cloud,
    )


def summarize(files: list[BigFile]) -> dict[str, tuple[int, int]]:
    """每个类型有多少个文件、共多少字节。筛选条上的数字用它。"""
    out: dict[str, tuple[int, int]] = {}
    for item in files:
        count, size = out.get(item.category, (0, 0))
        out[item.category] = (count + 1, size + item.size)
    return out


# ------------------------------------------------------------------ 删除


def delete_to_trash(
    files: list[BigFile],
    should_stop=lambda: False,
    on_progress=None,
) -> tuple[int, int, int, list[BigFile]]:
    """把这些文件送进回收站，返回 (成功数, 失败数, 释放字节, 剩下的文件)。

    送完一批就 `os.path.exists` 复查一次 —— `SHFileOperationW` 只给批次总数，
    不告诉你是哪个失败，逐个复查才能把「没删掉的」准确还给界面。
    """
    deleted = failed = freed = 0
    remaining: list[BigFile] = []
    total = len(files)

    for start in range(0, total, DELETE_BATCH):
        if should_stop():
            remaining.extend(files[start:])
            break
        chunk = files[start : start + DELETE_BATCH]
        allowed = [f for f in chunk if not safety.is_protected(f.path)]
        allowed_paths = {f.path for f in allowed}
        failed += len(chunk) - len(allowed)
        if allowed:
            trash.recycle([f.path for f in allowed])
        for item in chunk:
            if not os.path.exists(item.path):
                deleted += 1
                freed += item.size
                continue
            if item.path in allowed_paths:
                failed += 1
            remaining.append(item)
        if on_progress:
            done = min(start + DELETE_BATCH, total)
            on_progress(done, total, freed)

    return deleted, failed, freed, remaining
