"""卸载残留：找出「没有任何软件认领」的用户数据目录。

这是全项目风险最高的一项功能 —— 它的判断依据是**猜**：软件卸载了，
它留在 `%LOCALAPPDATA%` 里的缓存目录通常还在，但目录名和软件名之间
没有任何强制约定（VS Code 用 `Code`，微信 4.0 用 `xwechat_files`）。

所以这里的规则只有一条：**宁可漏掉一百个，也不能误伤一个。**

四道闸门，任何一道不过就不列：

1. **只看三个根的第一层子目录**。不递归、不进子目录 —— 一旦往下走，
   `Microsoft\\Windows\\...` 这种结构就会开始碰系统文件。
2. **硬白名单**。系统组件、运行时、公共容器的目录名写死在 `ALWAYS_KEEP`
   里，不做规则推导。推导出来的规则迟早会漏一个，漏的那个就是事故。
3. **认领判定**。已安装软件的名字（归一化后双向包含）和安装路径（前缀
   关系）都会让候选直接出局；发布商名也算认领。
4. **只列不勾选**。界面上默认全不选，删除进回收站，用户还能还原。

一句话：这个模块负责「找出来给你看」，不负责「替你决定删哪个」。
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field

from . import apps, safety, trash

# 三个根：(环境变量名, 界面上的中文标签)
ROOTS = (
    ("LOCALAPPDATA", "本地应用数据"),
    ("APPDATA", "漫游应用数据"),
    ("PROGRAMDATA", "程序公共数据"),
)

# 硬白名单：这些目录名永远不进候选。
#
# 加新条目的标准是「它是不是系统/运行时/公共容器」，不是「它大不大」。
# 判断不了的就不加 —— 漏一个比多一个安全。
ALWAYS_KEEP = {
    "microsoft",
    "packages",
    "temp",
    "tmp",
    "programs",
    "windows",
    "common files",
    "desktop",
    "documents",
    "downloads",
    "connecteddevicesplatform",
    "d3dscache",
    "placeholdertilelogos",
    "package cache",
    "uso shared",
    "usoshared",
    "usoprivate",
    "comms",
    "boost_interprocess",
    "regid.1991-06.com.microsoft",
    "microsoft corporation",
    "intel",
    "nvidia",
    "amd",
    "realtek",
}

# 名字长得像 GUID 的目录基本是 UWP / 组件的隔离存储，一律不碰
_GUID = re.compile(
    r"^\{?[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}\}?$",
    re.I,
)

# 扫描体积时的总时间预算。目录里可能有几十万个小文件，
# 算不完就先给个下限值，不为了一个数字卡住整页。
_SIZE_BUDGET = 6.0


@dataclass
class Leftover:
    path: str
    name: str
    root_label: str
    size: int = 0
    mtime: float = 0.0
    # 删除风险：low（久未改动，对应软件已不在）/ mid / high（最近动过）
    risk: str = "mid"
    note: str = ""

    @property
    def age_days(self) -> float:
        if not self.mtime:
            return 0.0
        return max(0.0, (time.time() - self.mtime) / 86400)


@dataclass
class ScanResult:
    items: list[Leftover] = field(default_factory=list)
    checked: int = 0          # 一共看了多少个一级目录
    claimed: int = 0          # 被已安装软件认领、跳过的
    kept: int = 0             # 命中硬白名单、跳过的
    truncated: bool = False   # 体积统计是否因超时被截断

    @property
    def total_size(self) -> int:
        return sum(i.size for i in self.items)


def _norm(text: str) -> str:
    """归一化名字用于比较：去空格、点、横线、括号和架构后缀。"""
    lowered = re.sub(r"[（(]\s*x?64\s*[)）]", "", (text or "").lower())
    return re.sub(r"[\s\-_.·]+", "", lowered)


def _environment_roots() -> list[tuple[str, str]]:
    found = []
    for env, label in ROOTS:
        path = os.environ.get(env, "")
        if path and os.path.isdir(path):
            found.append((path, label))
    return found


def _canon(path: str) -> str:
    return os.path.normcase(os.path.normpath(path)).rstrip("\\")


def _running_claims() -> set[str]:
    """正在运行的进程名，也是一份「认领证据」。

    `apkstart` 这种绿色软件既没写注册表、也没有卸载器，`list_installed`
    根本看不到它 —— 但它正在跑，进程名就是最硬的那条线索。少了这一条，
    一个正在使用、占 13 GB 的目录会被列成「卸载残留」。
    """
    names: set[str] = set()
    try:
        import psutil

        for proc in psutil.process_iter(["name", "exe"]):
            info = proc.info or {}
            for raw in (info.get("name"), os.path.basename(info.get("exe") or "")):
                stem = os.path.splitext(raw or "")[0]
                key = _norm(stem)
                # 进程名短于 4 个字（svc、db）没有区分度，不拿来当证据
                if len(key) >= 4:
                    names.add(key)
    except Exception:
        # 拿不到进程列表就少一层保护，不能因此让整页崩掉
        pass
    return names


def _program_folder_names() -> set[str]:
    """磁盘上存在的程序目录名。

    `E:\\ApkStart` 这种装在盘根、没写注册表、也没有卸载器的绿色软件，
    `list_installed` 看不见它，进程也没在跑 —— 但盘上确实有这个程序目录。
    同名目录存在，就说明这份数据有主，不能当残留。

    只扫每个根的第一层（几个目录，很便宜），不递归。
    """
    roots: list[str] = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        path = os.environ.get(env, "")
        if path and os.path.isdir(path):
            roots.append(path)
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        programs = os.path.join(local, "Programs")
        if os.path.isdir(programs):
            roots.append(programs)

    try:
        import psutil

        for part in psutil.disk_partitions(all=False):
            if "fixed" in part.opts or part.fstype:
                if os.path.isdir(part.mountpoint):
                    roots.append(part.mountpoint)
    except Exception:
        pass

    names: set[str] = set()
    for root in roots:
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            key = _norm(entry.name)
                            if len(key) >= 4:
                                names.add(key)
                    except OSError:
                        continue
        except OSError:
            continue
    return names


def build_claims(installed: list) -> tuple[set[str], list[str]]:
    """从已安装软件里整理出「认领证据」：名字集合 + 安装路径集合。

    名字按 `_norm` 归一化后存短名，另外把发行商也算进名字集合 ——
    `%LOCALAPPDATA%\\JetBrains` 这种按厂商建的目录靠产品名匹配不上。
    """
    names: set[str] = set()
    locations: list[str] = []
    for app in installed or []:
        for raw in (getattr(app, "name", ""), getattr(app, "publisher", "")):
            key = _norm(raw)
            # 太短的名字（"QQ"、"PC"）会产生大量误判，只保留 4 字以上的
            if len(key) >= 4:
                names.add(key)
        location = getattr(app, "location", "") or ""
        if location and os.path.isdir(location):
            locations.append(_canon(location))
    names |= _running_claims()
    names |= _program_folder_names()
    return names, locations


def _claimed_by_path(path: str, locations: list[str]) -> bool:
    canon = _canon(path)
    for location in locations:
        if canon == location or canon.startswith(location + os.sep):
            return True
    return False


def _claimed_by_name(name: str, names: set[str]) -> bool:
    key = _norm(name)
    if len(key) < 3:
        # 目录名太短（"db"、"tmp"）无法判断归属，按「可能有用」处理
        return True
    if key in names:
        return True
    # 双向包含：`Chrome` 之于 `googlechrome`、`wechat` 之于 `wechat4`
    for known in names:
        if len(key) >= 4 and (key in known or known in key):
            return True
    return False


def _dir_size(path: str, deadline: float) -> int:
    """递归累加目录体积，超时就带着已有结果返回。"""
    total = 0
    stack = [path]
    while stack:
        if time.monotonic() > deadline:
            break
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def _risk_for(mtime: float, size: int) -> tuple[str, str]:
    """按「最近有没有动过」判风险。

    判断残留最怕的是「软件还在用，只是名字对不上」。文件修改时间是目前
    唯一可靠的旁证：卸载半年没动过的目录，和昨天还在写的目录，风险完全不同。
    """
    age = (time.time() - mtime) / 86400 if mtime else 999
    if age < 7:
        return "high", "7 天内有改动，可能仍在使用"
    if age < 30:
        return "mid", "一个月内有过改动"
    if size < 10 * 1024 * 1024:
        return "low", "久未改动，但体积很小"
    return "low", "超一个月未改动，判定为卸载残留"


def scan(installed: list | None = None, report=None,
         should_stop=None) -> ScanResult:
    """列出候选残留。

    `installed` 传已安装软件列表；不传就自己扫一遍（慢，界面上不该这么用）。
    """
    if installed is None:
        installed = apps.list_installed(with_folders=True)

    result = ScanResult()
    names, locations = build_claims(installed)
    roots = _environment_roots()
    deadline = time.monotonic() + _SIZE_BUDGET

    candidates: list[tuple[str, str, str]] = []  # (path, name, root_label)
    for root_path, root_label in roots:
        try:
            with os.scandir(root_path) as entries:
                for entry in entries:
                    if should_stop and should_stop():
                        return result
                    try:
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    result.checked += 1
                    name = entry.name
                    if name.lower() in ALWAYS_KEEP or _GUID.match(name):
                        result.kept += 1
                        continue
                    if _claimed_by_path(entry.path, locations):
                        result.claimed += 1
                        continue
                    if _claimed_by_name(name, names):
                        result.claimed += 1
                        continue
                    # 系统目录兜底：safety 有自己的保护清单，别和它对着干
                    if safety.is_under_system_windows(entry.path):
                        result.kept += 1
                        continue
                    candidates.append((entry.path, name, root_label))
        except OSError:
            continue

    for index, (path, name, root_label) in enumerate(candidates):
        if should_stop and should_stop():
            return result
        if report:
            report(
                int(20 + 70 * index / max(1, len(candidates))),
                f"正在统计 {name} 的体积…",
            )
        try:
            stat = os.stat(path, follow_symlinks=False)
            mtime = stat.st_mtime
        except OSError:
            mtime = 0.0
        size = _dir_size(path, deadline)
        if time.monotonic() > deadline:
            result.truncated = True
        risk, note = _risk_for(mtime, size)
        result.items.append(
            Leftover(
                path=path,
                name=name,
                root_label=root_label,
                size=size,
                mtime=mtime,
                risk=risk,
                note=note,
            )
        )

    # 体积大的排前面：用户要的是「腾出空间」，不是「按字母看一遍」
    result.items.sort(key=lambda item: item.size, reverse=True)
    return result


def remove(paths: list[str]) -> tuple[int, int, int]:
    """把选中的目录送进回收站，返回 (成功数, 失败数, 释放字节)。

    这里**不信任界面传来的路径**：逐个重新校验父目录必须是那三个根之一，
    防止某天有人拿着构造出来的路径调进来。
    """
    roots = {_canon(path) for path, _label in _environment_roots()}
    safe: list[str] = []
    freed = 0
    for path in paths:
        canon = _canon(path)
        parent = _canon(os.path.dirname(canon))
        if parent not in roots:
            continue
        if safety.is_under_system_windows(canon):
            continue
        if not os.path.isdir(canon):
            continue
        freed += _dir_size(canon, time.monotonic() + 3.0)
        safe.append(path)

    if not safe:
        return 0, len(paths), 0

    ok, failed = trash.recycle(safe)
    return ok, failed, freed if ok else 0
