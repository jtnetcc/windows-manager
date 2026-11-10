"""Windows 已安装程序扫描。

两个来源：
1. 注册表 Uninstall 键 —— 正规安装包（MSI / NSIS / Inno）都会登记；
2. 安装目录扫描 —— 找不到带卸载器的目录就当成一款软件。

只靠注册表会漏掉一整类软件：国产游戏（梦幻西游、多数网游客户端）、
绿色版、用自家启动器安装且不写 Uninstall 键的程序。它们的特点恰恰是
「目录里躺着一个 uninstall.exe」，所以目录扫描这条线索很硬。

安全策略：只读注册表 + 打开程序自带的卸载向导，绝不静默执行卸载命令。
"""

from __future__ import annotations

import ctypes
import os
import re
import string
import struct
import subprocess
import time
import winreg
from ctypes import wintypes
from dataclasses import dataclass
from functools import lru_cache

# 显式指定 32/64 位视图。原来靠 WOW6432Node 路径区分，只在 64 位 Python 下成立；
# 32 位进程打开 HKLM\SOFTWARE 会被系统重定向到 WOW6432Node，
# 结果就是 64 位软件全部读不到、32 位读两遍。
_MACHINE_VIEWS = (
    (winreg.KEY_WOW64_64KEY, "本机"),
    (winreg.KEY_WOW64_32KEY, "本机"),
)

UNINSTALL_ROOTS = [
    (
        winreg.HKEY_LOCAL_MACHINE,
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        source,
        view,
    )
    for view, source in _MACHINE_VIEWS
] + [
    (
        winreg.HKEY_CURRENT_USER,
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        "当前用户",
        0,
    ),
]

# 这些不是「装了什么」，是系统补丁，混进列表只会干扰
_PATCH_PREFIX = (
    "update for ",
    "security update for ",
    "hotfix for ",
    "cumulative update for ",
)
_KB_RE = re.compile(r"\bKB\d{6,}\b", re.IGNORECASE)
# FILETIME 起点到 Unix 纪元的差值（100ns 为单位）
_FILETIME_EPOCH = 116444736000000000

# 卸载程序的常见叫法（小写比对）。NSIS 是 uninstall.exe / uninst.exe，
# Inno 是 unins000.exe，国产客户端喜欢直接叫「卸载.exe」。
_UNINSTALL_EXACT = {
    "uninstall.exe",
    "uninstall64.exe",
    "uninst.exe",
    "uninst64.exe",
    "uninstall_x64.exe",
    "unins000.exe",
    "unins001.exe",
    "卸载.exe",
    "卸载程序.exe",
    "uninstaller.exe",
}

# 目录扫描要绕开的地方：系统目录、回收站、临时与包缓存，
# 它们里面就算有 exe 也不是「用户装的软件」。
_SKIP_NAMES = {
    "windows",
    "windows.old",
    "windowsapps",
    "$recycle.bin",
    "system volume information",
    "recovery",
    "programdata",
    "users",
    "default",
    "perflogs",
    "temp",
    "tmp",
    "cache",
    "caches",
    "node_modules",
    ".git",
    ".svn",
    "installer",
    "packages",
    "logs",
    "crashdumps",
} | {f"windows{i}" for i in range(1, 6)}

# 目录名过于笼统时，宁可用上级目录名
_GENERIC_DIR_NAMES = {"app", "bin", "client", "game", "application", "program", "core", "main"}

# 目录扫描 + 体积统计的总预算（秒）。机械硬盘上大目录 walk 很慢，
# 超时就带着已扫到的结果返回，不能让用户对着进度条干等。
_FOLDER_BUDGET = 8.0


@dataclass
class InstalledApp:
    name: str
    version: str = ""
    publisher: str = ""
    location: str = ""
    size_kb: int = 0
    uninstall: str = ""
    quiet_uninstall: str = ""
    install_date: str = ""
    source: str = ""
    system: bool = False          # SystemComponent=1：属于别的程序的运行时/依赖

    @property
    def size_mb(self) -> float:
        return self.size_kb / 1024

    @property
    def removable(self) -> bool:
        """有没有可以拉起来的卸载向导。目录扫描出来的都有，注册表项可能没有。"""
        return bool(self.uninstall.strip())


def _query(key, name: str):
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _normalize_date(raw: str | None) -> str:
    if not raw or not str(raw).isdigit() or len(str(raw)) != 8:
        return ""
    text = str(raw)
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


def _key_last_write(key) -> str:
    """注册表键的最后写入时间，InstallDate 缺失时当安装日期用。

    Electron 系和企业部署包装完不写 InstallDate（WorkBuddy、DeepSeek 都是这样），
    但键写入时间基本等于安装/升级那一刻，比显示「—」有用得多。
    """
    filetime = wintypes.FILETIME()
    try:
        result = ctypes.windll.advapi32.RegQueryInfoKeyW(
            ctypes.c_void_p(int(key)),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            ctypes.byref(filetime),
        )
    except Exception:
        return ""
    if result != 0:
        return ""
    ticks = (filetime.dwHighDateTime << 32) | filetime.dwLowDateTime
    if ticks <= _FILETIME_EPOCH:
        return ""
    seconds = (ticks - _FILETIME_EPOCH) // 10_000_000
    return time.strftime("%Y-%m-%d", time.localtime(seconds))


def _looks_like_patch(name: str) -> bool:
    text = name.strip().lower()
    return text.startswith(_PATCH_PREFIX) or bool(_KB_RE.search(name))


# ----------------------------------------------------------------- 目录扫描


def _norm_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(path)).rstrip("\\/")


_EXE_IN_CMD = re.compile(r'^\s*(?:"([^"]+\.exe)"|([^\s"].*?\.exe))', re.IGNORECASE)


def _uninstall_dir(command: str) -> str:
    """从卸载命令里反推安装目录。

    很多安装包不写 InstallLocation，但 UninstallString 里带着完整路径
    （`"D:\\WorkBuddy\\Uninstall WorkBuddy.exe" /allusers`）。
    拿它当安装目录既能补上「打开安装目录」，也能和目录扫描对上号去重。
    """
    match = _EXE_IN_CMD.match(command or "")
    if not match:
        return ""
    exe = (match.group(1) or match.group(2) or "").strip()
    if not exe:
        return ""
    folder = os.path.dirname(exe)
    return folder if folder and os.path.isdir(folder) else ""


def _name_key(name: str) -> str:
    """比较名字时忽略空格、括号里的架构后缀，减少「同一个软件两个名字」。"""
    text = re.sub(r"[（(]\s*x?64\s*[)）]", "", name.lower())
    return re.sub(r"[\s\-_·]+", "", text).strip(".exe")


def _find_uninstaller(folder: str) -> str:
    """目录里有没有卸载程序，有就返回完整路径。"""
    try:
        names = os.listdir(folder)
    except OSError:
        return ""
    for raw in names:
        low = raw.lower()
        if not low.endswith(".exe"):
            continue
        if low in _UNINSTALL_EXACT or low.startswith(("uninst", "卸载")):
            full = os.path.join(folder, raw)
            try:
                if os.path.isfile(full):
                    return full
            except OSError:
                continue
    return ""


def _version_info(path: str) -> dict[str, str]:
    """读 exe 的版本资源（ProductName / FileVersion / CompanyName）。

    目录扫描出来的程序没有注册表值可读，公司名和版本只能从文件里挖。
    """
    try:
        size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return {}
        buffer = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(path, 0, size, buffer):
            return {}

        pointer = ctypes.c_void_p()
        length = ctypes.c_uint()
        if not ctypes.windll.version.VerQueryValueW(
            buffer,
            "\\VarFileInfo\\Translation",
            ctypes.byref(pointer),
            ctypes.byref(length),
        ):
            return {}
        if length.value < 4:
            return {}
        lang, codepage = struct.unpack("<HH", ctypes.string_at(pointer, 4))

        out: dict[str, str] = {}
        for field in ("ProductName", "FileVersion", "ProductVersion", "CompanyName"):
            sub = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\{field}"
            if not ctypes.windll.version.VerQueryValueW(
                buffer, sub, ctypes.byref(pointer), ctypes.byref(length)
            ):
                continue
            if not length.value:
                continue
            text = ctypes.wstring_at(pointer, length.value).rstrip("\x00").strip()
            if text:
                out[field] = text
        return out
    except Exception:
        return {}


_PLACEHOLDER_VERSIONS = {"", "0.0.0.0", "1.0.0.0", "1.0.0.1"}


@lru_cache(maxsize=64)
def exe_title(path: str) -> str:
    """exe 的显示名，拿不到就返回空串。

    版本资源里的 ProductName 比文件名靠谱：chrome.exe →「Google Chrome」，
    WeChat.exe →「微信」。文件占用提示里说「关闭微信」比「关闭 WeChat.exe」
    清楚得多。会读文件，所以带缓存。
    """
    if not path:
        return ""
    return _version_info(path).get("ProductName", "")


def _probe_folder_info(folder: str, uninstaller: str) -> dict[str, str]:
    """卸载器没写公司名/版本时，去目录里的主程序上找。

    国产客户端很常见：uninstall.exe 只有名字没有 CompanyName，
    但主程序（梦幻西游的 xyqsvc.exe）里写着 NetEase 和真实版本号。
    按体积从大到小试前几个 exe，凑齐就停。
    """
    info = _version_info(uninstaller)
    need_company = not info.get("CompanyName")
    need_version = (info.get("FileVersion") or "") in _PLACEHOLDER_VERSIONS
    if not (need_company or need_version):
        return info

    try:
        names = [n for n in os.listdir(folder) if n.lower().endswith(".exe")]
    except OSError:
        return info

    sized: list[tuple[int, str]] = []
    for name in names:
        try:
            sized.append((os.path.getsize(os.path.join(folder, name)), name))
        except OSError:
            pass
    sized.sort(reverse=True)

    skip = os.path.normcase(os.path.basename(uninstaller))
    for _size, name in sized[:12]:
        if os.path.normcase(name) == skip:
            continue
        candidate = _version_info(os.path.join(folder, name))
        if need_company and candidate.get("CompanyName"):
            info["CompanyName"] = candidate["CompanyName"]
            need_company = False
        if need_version:
            version = candidate.get("FileVersion") or candidate.get("ProductVersion", "")
            if version and version not in _PLACEHOLDER_VERSIONS:
                info["FileVersion"] = version
                need_version = False
        if not need_company and not need_version:
            break
    return info


def _dir_size(path: str, deadline: float) -> int:
    """累计目录体积（字节）。给了截止时间，超时就返回已统计的部分。"""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path, onerror=lambda e: None):
        if time.monotonic() > deadline:
            break
        for name in filenames:
            try:
                total += os.stat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return total


def _fixed_drives() -> list[str]:
    """本地固定磁盘（跳过光驱、U 盘、网络盘，扫它们又慢又没意义）。"""
    drives: list[str] = []
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for index, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << index):
            continue
        root = f"{letter}:\\"
        if ctypes.windll.kernel32.GetDriveTypeW(root) == 3:  # DRIVE_FIXED
            drives.append(root)
    return drives


def _scan_roots() -> list[tuple[str, int]]:
    """(起点, 往下几层)。盘符根目录浅一点，Program Files 里往往还套一层。"""
    roots: list[tuple[str, int]] = []
    for drive in _fixed_drives():
        roots.append((drive, 2))
        for sub in ("Program Files", "Program Files (x86)"):
            path = os.path.join(drive, sub)
            if os.path.isdir(path):
                roots.append((path, 3))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        programs = os.path.join(local, "Programs")
        if os.path.isdir(programs):
            roots.append((programs, 2))
    return roots


def _iter_dirs(root: str, max_depth: int):
    stack: list[tuple[str, int]] = [(root, 0)]
    while stack:
        path, depth = stack.pop()
        if depth >= max_depth:
            continue
        try:
            entries = list(os.scandir(path))
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            low = entry.name.lower()
            if low.startswith("$") or low in _SKIP_NAMES:
                continue
            yield entry.path
            stack.append((entry.path, depth + 1))


def _folder_apps(deadline: float) -> list[InstalledApp]:
    """扫盘找「带卸载器的目录」，当成一个已安装程序。

    梦幻西游这类客户端不写注册表，但会在安装目录里放 uninstall.exe，
    这是它们留下的唯一确定性痕迹。
    """
    seen: set[str] = set()
    result: list[InstalledApp] = []

    for root, depth in _scan_roots():
        if time.monotonic() > deadline:
            break
        for folder in _iter_dirs(root, depth):
            normalized = _norm_path(folder)
            if normalized in seen:
                continue
            seen.add(normalized)
            if time.monotonic() > deadline:
                break
            uninstaller = _find_uninstaller(folder)
            if not uninstaller:
                continue

            folder_name = os.path.basename(folder.rstrip("\\/"))
            info = _probe_folder_info(folder, uninstaller)
            name = folder_name
            if name.lower() in _GENERIC_DIR_NAMES:
                name = info.get("ProductName") or os.path.basename(
                    os.path.dirname(folder.rstrip("\\/"))
                )

            try:
                created = os.stat(uninstaller).st_ctime
                install_date = time.strftime("%Y-%m-%d", time.localtime(created))
            except OSError:
                install_date = ""

            size_bytes = _dir_size(folder, deadline)
            result.append(
                InstalledApp(
                    name=name,
                    version=info.get("FileVersion") or info.get("ProductVersion", ""),
                    publisher=info.get("CompanyName", ""),
                    location=folder,
                    size_kb=size_bytes // 1024,
                    uninstall=uninstaller,
                    install_date=install_date,
                    source="安装目录",
                )
            )
    return result


def list_installed(with_folders: bool = True, report=None) -> list[InstalledApp]:
    """扫描注册表与安装目录，返回去重后的已安装程序列表。

    `report(percent, text)` 可选，给任务中心报进度。
    """
    found: dict[str, InstalledApp] = {}
    by_path: dict[str, str] = {}
    by_name: dict[str, str] = {}

    def remember(app: InstalledApp) -> None:
        key = f"{app.name}|{app.version}".lower()
        found[key] = app
        if app.location:
            by_path[_norm_path(app.location)] = key
        by_name.setdefault(_name_key(app.name), key)

    for hive, path, source, view in UNINSTALL_ROOTS:
        try:
            root = winreg.OpenKey(hive, path, 0, winreg.KEY_READ | view)
        except OSError:
            continue
        try:
            index = 0
            while True:
                try:
                    sub_name = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                try:
                    sub = winreg.OpenKey(root, sub_name, 0, winreg.KEY_READ | view)
                except OSError:
                    continue
                try:
                    name = _query(sub, "DisplayName")
                    if not name:
                        continue
                    if _query(sub, "ParentKeyName"):
                        continue
                    release = str(_query(sub, "ReleaseType") or "").lower()
                    if release in ("update", "hotfix", "security update"):
                        continue
                    if _looks_like_patch(str(name)):
                        continue

                    # 没有 UninstallString 也留着：用户问的是「装了什么」，
                    # 不是「能卸什么」。这类项在界面上标成不可卸载即可。
                    uninstall_cmd = str(_query(sub, "UninstallString") or "").strip()
                    location = str(_query(sub, "InstallLocation") or "").strip()
                    app = InstalledApp(
                        name=str(name).strip(),
                        version=str(_query(sub, "DisplayVersion") or "").strip(),
                        publisher=str(_query(sub, "Publisher") or "").strip(),
                        location=location or _uninstall_dir(uninstall_cmd),
                        size_kb=int(_query(sub, "EstimatedSize") or 0),
                        uninstall=uninstall_cmd,
                        quiet_uninstall=str(_query(sub, "QuietUninstallString") or "").strip(),
                        install_date=(
                            _normalize_date(_query(sub, "InstallDate"))
                            or _key_last_write(sub)
                        ),
                        source=source,
                        system=_query(sub, "SystemComponent") == 1,
                    )
                finally:
                    winreg.CloseKey(sub)

                key = f"{app.name}|{app.version}".lower()
                if key not in found or (app.size_kb > found[key].size_kb):
                    found[key] = app
                    if app.location:
                        by_path[_norm_path(app.location)] = key
                    by_name.setdefault(_name_key(app.name), key)
        finally:
            winreg.CloseKey(root)

    if with_folders:
        if report:
            report(55, "正在扫描安装目录…")
        for app in _folder_apps(time.monotonic() + _FOLDER_BUDGET):
            path_key = _norm_path(app.location) if app.location else ""
            name_key = _name_key(app.name)
            existing = by_path.get(path_key) or by_name.get(name_key)
            if existing is None:
                remember(app)
                continue
            # 注册表那份是「正规安装」的记录，保留它的名字和版本；
            # 卸载命令、安装位置、体积、日期则可以互相补齐。
            base = found[existing]
            if not base.uninstall and app.uninstall:
                base.uninstall = app.uninstall
            if not base.location:
                base.location = app.location
            if not base.size_kb:
                base.size_kb = app.size_kb
            if not base.publisher:
                base.publisher = app.publisher
            if not base.install_date:
                base.install_date = app.install_date
            if path_key:
                by_path[path_key] = existing
        if report:
            report(90, "正在整理结果…")

    return sorted(found.values(), key=lambda a: a.name.lower())


def launch_uninstaller(app: InstalledApp) -> tuple[bool, str]:
    """打开程序自带的卸载向导。"""
    command = app.uninstall.strip()
    if not command:
        return False, "该程序没有可用的卸载命令"
    # 目录扫描得到的是一条裸路径，含空格时必须自己加引号，
    # shell=True 会把「D:\Some App\uninstall.exe」拆成两段。
    if not command.startswith('"') and os.path.isfile(command):
        command = f'"{command}"'
    try:
        subprocess.Popen(command, shell=True)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def open_install_dir(app: InstalledApp) -> tuple[bool, str]:
    path = app.location.strip().strip('"')
    if not path or not os.path.isdir(path):
        return False, "安装目录不存在或未记录"
    try:
        os.startfile(path)
        return True, ""
    except Exception as exc:
        return False, str(exc)
