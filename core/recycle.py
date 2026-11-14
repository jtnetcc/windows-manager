"""回收站管理：列出内容、还原、清空。

不走 COM（项目没装 pywin32），直接解析 `$Recycle.Bin` 里的两种文件：

- `$R######.ext` —— 被删除的实际数据
- `$I######.ext` —— 元数据：原始路径、文件大小、删除时间

`$I` 的格式（v2，Win10/11 默认）：

    offset 0   8 字节   header = 2
    offset 8   8 字节   原始大小
    offset 16  8 字节   删除时间（FILETIME）
    offset 24  4 字节   路径长度（字符数，含结尾 \0）
    offset 28  ...      UTF-16LE 的原始完整路径

v1（XP 时代）是固定 520 字节的路径区，这里保留一个兜底分支。

**还原**就是把 `$R` 文件搬回原路径，再删掉 `$I`。目标已存在时不覆盖，
自动加「(1)」后缀，避免用户丢东西。
"""

from __future__ import annotations

import ctypes
import os
import struct
from ctypes import wintypes
from dataclasses import dataclass
from . import applog

# 回收站文件夹的 Known Folder ID
FOLDERID_RecycleBinFolder = "{B7534046-3ECB-4C18-BE4E-64CD4CB7D6AC}"

_SHGFP = 0x00008000  # KF_FLAG_DEFAULT


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(text: str) -> _GUID:
    import uuid

    return _GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


def bin_path() -> str:
    """当前用户的回收站目录。拿不到就退回 SystemDrive 下的默认位置。"""
    try:
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]

        folder = _guid(FOLDERID_RecycleBinFolder)
        ptr = ctypes.c_wchar_p()
        if shell32.SHGetKnownFolderPath(
            ctypes.byref(folder), _SHGFP, None, ctypes.byref(ptr)
        ) == 0:
            value = ptr.value or ""
            ole32.CoTaskMemFree(ptr)
            if value:
                return value
    except Exception:
        applog.failed("recycle.bin_path")
    return os.path.join(os.environ.get("SystemDrive", "C:") + "\\", "$Recycle.Bin")


@dataclass
class Item:
    name: str            # 原始文件名
    origin: str          # 原始完整路径
    size: int            # 字节
    deleted_at: float    # unix 时间戳
    data_path: str       # $R 文件路径
    info_path: str       # $I 文件路径

    @property
    def folder(self) -> str:
        return os.path.dirname(self.origin)


def _filetime_to_unix(value: int) -> float:
    if value <= 0:
        return 0.0
    return value / 10_000_000 - 11644473600


def _parse_info(path: str) -> tuple[str, int, float] | None:
    """解析 $I 文件，返回 (原始路径, 大小, 删除时间)。"""
    try:
        with open(path, "rb") as handle:
            raw = handle.read(600)
    except OSError:
        return None
    if len(raw) < 28:
        return None

    try:
        version = struct.unpack_from("<q", raw, 0)[0]
        size = struct.unpack_from("<q", raw, 8)[0]
        deleted = _filetime_to_unix(struct.unpack_from("<q", raw, 16)[0])
    except struct.error:
        return None

    if version == 2:
        length = struct.unpack_from("<i", raw, 24)[0]
        if length <= 0 or length > 1000:
            return None
        text = raw[28 : 28 + length * 2]
        origin = text.decode("utf-16-le", "replace").split("\x00")[0]
    else:
        # v1：路径区从 24 开始，固定 520 字节
        text = raw[24 : 24 + 520]
        origin = text.decode("utf-16-le", "replace").split("\x00")[0]

    if not origin:
        return None
    return origin, max(0, size), deleted


def list_items() -> list[Item]:
    """列出回收站里的项目，按体积从大到小排。"""
    root = bin_path()
    items: list[Item] = []

    for folder, _dirs, files in os.walk(root, onerror=lambda _e: None):
        # 只看 SID 那一层的 $I / $R 文件
        info_files = [f for f in files if f.startswith("$I")]
        for info_name in info_files:
            info_path = os.path.join(folder, info_name)
            data_name = "$R" + info_name[2:]
            data_path = os.path.join(folder, data_name)
            parsed = _parse_info(info_path)
            if parsed is None:
                continue
            origin, size, deleted = parsed

            if not os.path.exists(data_path):
                continue
            if size <= 0:
                try:
                    size = os.path.getsize(data_path)
                except OSError:
                    size = 0

            items.append(
                Item(
                    name=os.path.basename(origin),
                    origin=origin,
                    size=size,
                    deleted_at=deleted,
                    data_path=data_path,
                    info_path=info_path,
                )
            )

    items.sort(key=lambda i: i.size, reverse=True)
    return items


# ---------------------------------------------------------------- 操作


def _unique_path(path: str) -> str:
    """目标已存在时加 (1)(2) 后缀，绝不覆盖。"""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    for index in range(1, 100):
        candidate = f"{stem} ({index}){ext}"
        if not os.path.exists(candidate):
            return candidate
    return f"{stem} ({os.getpid()}){ext}"


def restore(items: list[Item]) -> tuple[int, int]:
    """还原到原位置，返回 (成功, 失败)。"""
    import shutil

    ok = 0
    failed = 0
    for item in items:
        try:
            target = _unique_path(item.origin)
            parent = os.path.dirname(target)
            if parent and not os.path.isdir(parent):
                os.makedirs(parent, exist_ok=True)

            if os.path.isdir(item.data_path):
                shutil.move(item.data_path, target)
            else:
                shutil.move(item.data_path, target)

            try:
                os.remove(item.info_path)
            except OSError:
                applog.silenced("recycle.restore")
            ok += 1
        except (OSError, shutil.Error):
            failed += 1
    return ok, failed


def empty() -> bool:
    """清空回收站。用系统 API，走的是和右键菜单一样的路径。"""
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.SHEmptyRecycleBinW.restype = ctypes.c_long
        # SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
        flags = 0x00000001 | 0x00000002 | 0x00000004
        return shell32.SHEmptyRecycleBinW(None, None, flags) == 0
    except Exception:
        return False
