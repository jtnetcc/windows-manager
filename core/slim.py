"""系统瘦身：关掉用不上的系统功能，收回能安全释放的空间。

和「垃圾清理」的分工：那边删的是缓存文件，这边改的是系统行为（更新策略、
遥测服务、推荐位）与系统自己生成的大文件（休眠文件、转储、旧系统）。

三条原则：

1. **只写已知的策略/偏好值**，不删系统文件、不改系统目录权限。
2. **每一项都可逆**，页面上把「关掉会发生什么」写在名字下面；恢复默认只是
   把值删掉，回到 Windows 自己的默认。
3. **不碰影响系统启动与安全的核心功能**：Windows Defender、防火墙、系统还原、
   页面文件、BITS、搜索索引都不在列表里 —— 它们省的那点资源换来的是别的问题。
"""

from __future__ import annotations

import os
import subprocess
import winreg
from dataclasses import dataclass
from typing import Callable

from .winpath import HKCU, HKLM, hive, system_drive

CREATE_NO_WINDOW = 0x08000000
_SYSTEM32 = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32")

# 分组：key → (标题, 一句话说明)
GROUPS: list[tuple[str, str, str]] = [
    ("update", "系统更新", "只是不再自动下载安装，需要时仍可手动检查更新。"),
    ("service", "后台与诊断", "少几个常驻服务在后台跑，少一点上传。"),
    ("noise", "推荐与广告", "关掉系统里的应用推荐、使用提示与广告标识。"),
    ("space", "空间回收", "只动系统自己生成的转储与旧文件，删了不会影响运行。"),
]


# ---------------------------------------------------------------- 注册表读写


def reg_get(path: str, value: str):
    """读一个值；键或值不存在都返回 None。"""
    hive_name, sub = path.split("\\", 1)
    try:
        with winreg.OpenKey(hive(hive_name), sub, 0, winreg.KEY_READ) as key:
            data, _kind = winreg.QueryValueEx(key, value)
            return data
    except OSError:
        return None


def reg_set(path: str, value: str, data) -> None:
    """写一个值；`data=None` 表示删掉这个值（回到系统默认）。

    删的时候键可能整个不存在（本来就没改过），这时什么都不用做 ——
    Windows 没有 CreateKey 过的键，读出来就是默认值。
    """
    hive_name, sub = path.split("\\", 1)
    if data is None:
        try:
            with winreg.OpenKey(hive(hive_name), sub, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, value)
        except FileNotFoundError:
            pass
        return
    with winreg.CreateKeyEx(hive(hive_name), sub, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, value, 0, winreg.REG_DWORD, int(data))


def _flag(path: str, value: str, off: int, on: int | None = None,
          default: bool = True):
    """单值开关：返回 (read, turn_off, turn_on)。

    `default` 是「值不存在时算不算开着」。绝大多数功能默认开，所以缺省 True。
    """

    def read() -> bool:
        raw = reg_get(path, value)
        return default if raw is None else raw != off

    return read, lambda: reg_set(path, value, off), lambda: reg_set(path, value, on)


def _flags(pairs: list[tuple[str, str, int]]):
    """多值开关：全部关掉才算关。推荐位分散在五六个值里，逐个写太啰嗦。"""

    def read() -> bool:
        # 值不存在 = 没改过 = 功能开着。写成 `(raw or 0) != off` 会把
        # 「键上没有这个值」当成 0，进而误判成已关闭。
        return all(
            (raw := reg_get(path, value)) is None or raw != off
            for path, value, off in pairs
        )

    return (
        read,
        lambda: [reg_set(path, value, off) for path, value, off in pairs],
        lambda: [reg_set(path, value, None) for path, value, off in pairs],
    )


def _run(*args: str, timeout: int = 60) -> tuple[int, str]:
    """跑一条系统命令，返回 (退出码, 合并后的输出)。"""
    try:
        done = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        text = (done.stdout or "") + (done.stderr or "")
        return done.returncode, text.strip()
    except subprocess.TimeoutExpired:
        return -1, "命令超时"
    except OSError as exc:
        return -1, str(exc)


def _sc(*args: str) -> None:
    _run(os.path.join(_SYSTEM32, "sc.exe"), *args, timeout=20)


def _service(name: str, restore_start: str = "auto"):
    """服务开关：Start=4 是禁用。

    停服务之前先 `config start= disabled`，顺序反过来的话，停掉的自动启动服务
    会被系统立刻拉起来。
    """
    path = rf"SYSTEM\CurrentControlSet\Services\{name}"

    def read() -> bool:
        raw = reg_get(HKLM + "\\" + path, "Start")
        return raw is None or raw != 4

    def turn_off() -> None:
        _sc("config", name, "start=", "disabled")
        _sc("stop", name)

    def turn_on() -> None:
        _sc("config", name, "start=", restore_start)

    return read, turn_off, turn_on


# ---------------------------------------------------------------- 开关项


@dataclass(frozen=True)
class Toggle:
    key: str
    group: str
    name: str
    effect: str
    read: Callable[[], bool]
    turn_off: Callable[[], None]
    turn_on: Callable[[], None]
    admin: bool = False


_UPDATE = r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU"
_DELIVERY = r"SOFTWARE\Policies\Microsoft\Windows\DeliveryOptimization"
_TELEMETRY = r"SOFTWARE\Policies\Microsoft\Windows\DataCollection"
_CDM = r"SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
_BG = r"SOFTWARE\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications"

_auto_update = _flag(HKLM + "\\" + _UPDATE, "NoAutoUpdate", 1)
_auto_restart = _flag(HKLM + "\\" + _UPDATE, "NoAutoRebootWithLoggedOnUsers", 1)
_p2p = _flag(HKLM + "\\" + _DELIVERY, "DODownloadMode", 0)
_telemetry = _flag(HKLM + "\\" + _TELEMETRY, "AllowTelemetry", 0)
_diagtrack = _service("DiagTrack", "auto")
_wer = _service("WerSvc", "demand")
_background = _flag(HKCU + "\\" + _BG, "GlobalUserDisabled", 1, 0)
# 搜索的网页建议有两个开关：用户级的 BingSearchEnabled（自己就能改）和
# 组策略里的 DisableSearchBoxSuggestions。新版 Windows 把 HKCU\Software\Policies
# 锁给了管理员（普通用户 CreateKey 直接拒绝访问），所以以用户级那个为准，
# 策略能写就顺手写上，写不了不算失败 —— 提权运行时两者都会生效。
_SEARCH_UI = HKCU + r"\SOFTWARE\Microsoft\Windows\CurrentVersion\Search"
_SEARCH_POLICY = HKCU + r"\SOFTWARE\Policies\Microsoft\Windows\Explorer"


def _search_read() -> bool:
    raw = reg_get(_SEARCH_UI, "BingSearchEnabled")
    return True if raw is None else raw != 0


def _search_off() -> None:
    reg_set(_SEARCH_UI, "BingSearchEnabled", 0)
    try:
        reg_set(_SEARCH_POLICY, "DisableSearchBoxSuggestions", 1)
    except PermissionError:
        pass


def _search_on() -> None:
    reg_set(_SEARCH_UI, "BingSearchEnabled", None)
    try:
        reg_set(_SEARCH_POLICY, "DisableSearchBoxSuggestions", None)
    except PermissionError:
        pass
_suggestions = _flags(
    [
        (HKCU + "\\" + _CDM, "SubscribedContent-338388Enabled", 0),  # 开始菜单建议
        (HKCU + "\\" + _CDM, "SubscribedContent-338389Enabled", 0),  # Windows 提示
        (HKCU + "\\" + _CDM, "SubscribedContent-338393Enabled", 0),  # 设置建议
        (HKCU + "\\" + _CDM, "SoftLandingEnabled", 0),               # 欢迎体验
        (HKCU + "\\" + _CDM, "SystemPaneSuggestionsEnabled", 0),     # 设置磁贴
    ]
)
_ad_id = _flag(
    HKCU + r"\SOFTWARE\Microsoft\Windows\CurrentVersion\AdvertisingInfo", "Enabled", 0
)


def _both(first, second) -> tuple[Callable, Callable, Callable]:
    """两个值合成一个开关：任一为关就算关，打开时两个都恢复默认。"""
    read_a, off_a, on_a = first
    read_b, off_b, on_b = second

    def read() -> bool:
        return read_a() and read_b()

    def turn_off() -> None:
        off_a()
        off_b()

    def turn_on() -> None:
        on_a()
        on_b()

    return read, turn_off, turn_on


TOGGLES: list[Toggle] = [
    Toggle(
        "update_auto",
        "update",
        "自动下载并安装更新",
        "补丁不再自动安装，需要时手动检查。",
        *_auto_update,
        admin=True,
    ),
    Toggle(
        "update_restart",
        "update",
        "更新后自动重启",
        "装完只提示，不会在你正用电脑时自己重启。",
        *_auto_restart,
        admin=True,
    ),
    Toggle(
        "update_share",
        "update",
        "向其他电脑分发更新",
        "只从微软下载，不再占用上传带宽。",
        *_p2p,
        admin=True,
    ),
    Toggle(
        "telemetry",
        "service",
        "诊断数据与体验服务",
        "停掉遥测服务，诊断数据降到最低档。",
        *_both(_telemetry, _diagtrack),
        admin=True,
    ),
    Toggle(
        "error_report",
        "service",
        "Windows 错误报告",
        "崩溃时不再收集上报，少一个弹窗。",
        *_wer,
        admin=True,
    ),
    Toggle(
        "background_apps",
        "service",
        "商店应用后台运行",
        "退到后台即暂停，省电省内存，推送会延迟。",
        *_background,
    ),
    Toggle(
        "search_web",
        "service",
        "搜索框的网页建议",
        "开始菜单只搜本机，不再联网取建议。",
        _search_read,
        _search_off,
        _search_on,
    ),
    Toggle(
        "suggestions",
        "noise",
        "应用推荐与使用提示",
        "不再推「推荐应用」和「使用提示」。",
        *_suggestions,
    ),
    Toggle(
        "ad_id",
        "noise",
        "广告标识",
        "应用拿不到广告 ID，跨应用投放不再关联你。",
        *_ad_id,
    ),
]

BY_KEY: dict[str, Toggle] = {item.key: item for item in TOGGLES}


def states() -> dict[str, bool]:
    """所有开关的当前状态（True = 功能开着）。"""
    out: dict[str, bool] = {}
    for item in TOGGLES:
        try:
            out[item.key] = item.read()
        except Exception:  # 单个读失败不该让整页空白
            out[item.key] = True
    return out


def apply(key: str, on: bool) -> str:
    """执行一个开关，返回错误文案；空串表示成功。"""
    item = BY_KEY.get(key)
    if item is None:
        return "没有这一项"
    try:
        (item.turn_on if on else item.turn_off)()
    except PermissionError:
        # winreg 的 WinError 5 在 Python 里是 PermissionError，原文「拒绝访问」
        # 看不出是权限问题，界面上直接说人话
        return "需要管理员权限"
    except OSError as exc:
        return str(exc)
    return ""


def disabled_count() -> int:
    return sum(1 for value in states().values() if not value)


# ---------------------------------------------------------------- 空间回收


def _walk_size(path: str, limit: int = 60000) -> int:
    """目录体积。带文件数上限：Windows.old 能有十几万个文件，全走一遍要好几秒。"""
    total = 0
    seen = 0
    for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
            seen += 1
            if seen >= limit:
                return total
    return total


def hiberfil_path() -> str:
    return system_drive() + "hiberfil.sys"


def hibernation_on() -> bool:
    raw = reg_get(HKLM + r"\SYSTEM\CurrentControlSet\Control\Power", "HibernateEnabled")
    if raw is not None:
        return bool(raw)
    return os.path.exists(hiberfil_path())


def _hiber_size() -> int:
    try:
        return os.path.getsize(hiberfil_path())
    except OSError:
        return 0


def _hiber_off() -> tuple[int, str]:
    before = _hiber_size()
    code, text = _run(os.path.join(_SYSTEM32, "powercfg.exe"), "/h", "off")
    if code != 0:
        return 0, text or "关闭休眠失败"
    return before, "已关闭休眠，文件已删除；需要时点「重新开启」"


def _hiber_on() -> None:
    _run(os.path.join(_SYSTEM32, "powercfg.exe"), "/h", "on")


def _windows_old() -> str:
    return system_drive() + "Windows.old"


def _old_size() -> int:
    path = _windows_old()
    return _walk_size(path) if os.path.isdir(path) else 0


def _old_clear() -> tuple[int, str]:
    path = _windows_old()
    if not os.path.isdir(path):
        return 0, "目录不存在，无需处理"
    before = _walk_size(path)
    # 这个目录归 TrustedInstaller 所有，先拿所有权再删；直接 rd 会全部「拒绝访问」
    _run("takeown", "/F", path, "/R", "/A", "/D", "Y", timeout=600)
    _run("icacls", path, "/grant", "*S-1-5-32-544:(OI)(CI)F", "/T", "/Q", timeout=600)
    code, text = _run("cmd.exe", "/c", "rd", "/s", "/q", path, timeout=600)
    left = _walk_size(path) if os.path.isdir(path) else 0
    if left:
        return max(0, before - left), f"还有 {left / 1024 ** 3:.1f} GB 没删掉，可能被占用：{text[:80]}"
    return before, "已删除旧系统文件，不能再回退到上一个 Windows 版本"


def _dump_files() -> list[str]:
    windir = os.environ.get("WINDIR", r"C:\Windows")
    out = [os.path.join(windir, "MEMORY.DMP")]
    mini = os.path.join(windir, "Minidump")
    if os.path.isdir(mini):
        out += [os.path.join(mini, name) for name in os.listdir(mini)]
    return [path for path in out if os.path.isfile(path)]


def _dump_size() -> int:
    total = 0
    for path in _dump_files():
        try:
            total += os.path.getsize(path)
        except OSError:
            continue
    return total


def _dump_clear() -> tuple[int, str]:
    freed = 0
    for path in _dump_files():
        try:
            size = os.path.getsize(path)
            os.remove(path)
            freed += size
        except OSError:
            continue
    if not freed:
        return 0, "没有可删除的转储文件"
    return freed, "已删除转储文件；下次蓝屏仍会生成新的"


def _winsxs_clear() -> tuple[int, str]:
    # 不带 /ResetBase：保留「卸载最近更新」的能力，只清被替换掉的旧组件
    code, text = _run(
        os.path.join(_SYSTEM32, "Dism.exe"),
        "/Online",
        "/Cleanup-Image",
        "/StartComponentCleanup",
        timeout=1800,
    )
    if code != 0:
        tail = text.splitlines()[-1] if text else "DISM 返回失败"
        return 0, f"组件清理失败：{tail[:90]}"
    return 0, "组件清理完成；被替换掉的旧版本已回收（不再能卸载旧更新）"


@dataclass(frozen=True)
class Space:
    key: str
    name: str
    effect: str
    measure: Callable[[], int]
    clear: Callable[[], tuple[int, str]]
    admin: bool = True
    danger: bool = False
    confirm: str = ""
    action: str = "清理"
    # 可逆项（目前只有休眠）：能判断当前是否启用、也能重新打开
    enabled: Callable[[], bool] | None = None
    restore: Callable[[], None] | None = None
    restore_text: str = "重新开启"
    known_size: bool = True   # False = 体积由系统决定，界面上不显示数字


SPACES: list[Space] = [
    Space(
        "hibernation",
        "休眠文件",
        "和内存等量。关掉不能休眠，快速启动也失效（开机慢几秒）。",
        _hiber_size,
        _hiber_off,
        enabled=hibernation_on,
        restore=_hiber_on,
        action="关闭休眠",
        restore_text="重新开启",
    ),
    Space(
        "windows_old",
        "旧系统文件",
        "升级留下的旧系统，常有几 GB。删掉后不能再回退到上一版。",
        _old_size,
        _old_clear,
        danger=True,
        confirm="删除后将无法回退到上一个 Windows 版本，确认系统运行正常再继续。",
        action="删除",
    ),
    Space(
        "dump",
        "内存转储",
        "蓝屏时的内存快照，只用于排查崩溃，删了下次崩溃会重新生成。",
        _dump_size,
        _dump_clear,
    ),
    Space(
        "winsxs",
        "组件存储",
        "更新替换下的旧组件。清理后旧更新不能再卸载。",
        lambda: 0,
        _winsxs_clear,
        danger=True,
        confirm="清理过程需要几分钟，期间请不要关闭本程序。清理后不能再卸载已安装的更新。",
        action="清理",
        known_size=False,
    ),
]

SPACE_BY_KEY: dict[str, Space] = {item.key: item for item in SPACES}


def scan() -> dict[str, int]:
    """统计空间项的体积。Windows.old 可能稍慢，调用方应放进任务线程。"""
    out: dict[str, int] = {}
    for item in SPACES:
        try:
            out[item.key] = item.measure()
        except Exception:
            out[item.key] = 0
    return out


def clear(key: str) -> tuple[int, str]:
    item = SPACE_BY_KEY.get(key)
    if item is None:
        return 0, "没有这一项"
    return item.clear()

