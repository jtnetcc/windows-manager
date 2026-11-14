"""垃圾清理：规则、扫描、删除。

三条来自实战的原则：

1. **扫描阶段就把「删不掉的东西」过滤掉**。桌面临时标记文件（desktop.ini）、
   快捷方式（.lnk）、被占用的 .tmp 文件、Windows 高危子目录，全部在扫描时就跳过。
   否则清理结束会弹「N 项失败」，用户以为工具坏了。

2. **串行扫描，不并发**。多个目录同时深度递归会造成磁盘 IO 争用，反而更慢。
   这里用生成器分批产出，调用方（QThread）每批 sleep(0) 让权，保证能取消。

3. **删除前必须过安全层**。`safety.is_protected()` 是最后一道防线，
   即便规则写错了也不会删到系统目录。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from . import safety
from . import applog

BATCH = 60
MAX_FILES_PER_RULE = 40000

# 删了没有任何空间收益，Windows 会自己重建
SYSTEM_MARKER_FILES = {"desktop.ini", "thumbs.db", "ehthumbs.db", "ehthumbs_vista.db"}

# 容易被占用、删除易失败的临时文件
LOCK_PRONE_SUFFIX = (".tmp", ".temp", ".lock", ".partial", "~")

# 系统盘 Windows 下「动了会出事」的位置
WINDOWS_RISKY_SUBPATHS = (
    "system32\\config",
    "system32\\catroot",
    "system32\\catroot2",
    "system32\\drivers",
    "system32\\driverstore",
    "system32\\wbem",
    "servicing",
    "winsxs",
    "\\boot\\",
    "system32\\tasks",
    "system32\\grouppolicy",
    "system32\\winevt",
    "system32\\spool",
    "assembly",
    "microsoft.net",
    "systemresources",
    "appcompat",
    "system32\\spp",
)

RISKY_EXT = {".sys", ".cat", ".drv", ".dll", ".exe", ".msi", ".msp"}

# 这几条属于「系统更新积压」，体检里的对应因子要用
UPDATE_RULE_IDS = {"windows_update_download", "delivery_optimization"}


@dataclass
class Rule:
    id: str
    name: str
    category: str
    paths: list[str]
    max_age_days: int = 0
    recursive: bool = True
    # 落在系统目录下时，显式声明允许清理的根目录（安全层要用）
    allow_prefixes: list[str] = field(default_factory=list)
    note: str = ""
    # 没有管理员权限时删不掉。程序默认以管理员身份启动，界面上不再提示，
    # 只在「从源码直接跑」这种普通权限场景里退回默认不勾选。
    needs_admin: bool = False
    # 删了有实际影响（比如社交软件的已收图片/视频），默认不勾，让用户自己决定
    default_off: bool = False


@dataclass
class FileItem:
    path: str
    size: int
    rule_id: str
    category: str


@dataclass
class RuleResult:
    rule_id: str
    name: str
    category: str
    count: int = 0
    size: int = 0
    skipped: int = 0

    @property
    def empty(self) -> bool:
        return self.count == 0


def _env(name: str) -> str:
    return os.environ.get(name, "")


def _children(parent: str, prefix: str = "") -> list[str]:
    """列出 parent 下名字以 prefix 开头的子目录，按名字排序。

    Chromium 系的 `User Data` 里除了 `Default` 还可能有 `Profile 1`、`Profile 2`，
    Store 应用则是一个包一个目录 —— 这些都没法写死路径，只能扫描时现查。
    读不到就返回空列表，不让一条规则把整轮扫描带崩。
    """
    out: list[str] = []
    try:
        for entry in os.scandir(parent):
            if not entry.is_dir():
                continue
            if prefix and not entry.name.lower().startswith(prefix.lower()):
                continue
            out.append(entry.path)
    except OSError:
        return []
    return sorted(out)


# Chromium 系的缓存目录。原来只收 `Cache`，实际 `Code Cache` 和 `GPUCache`
# 常常比它更大，尤其是装了扩展、常看视频的浏览器。
CHROMIUM_CACHE_DIRS = (
    "Cache",
    "Code Cache",
    "GPUCache",
    "ShaderCache",
    "GrShaderCache",
    "DawnCache",
    r"Service Worker\CacheStorage",
    r"Service Worker\ScriptCache",
)


def _chromium_caches(local: str, user_data: str) -> list[str]:
    """一个 Chromium 浏览器下所有「可删且会重建」的缓存目录（含多用户配置）。"""
    base = os.path.join(local, user_data)
    profiles = [os.path.join(base, "Default"), *_children(base, "Profile")]
    paths: list[str] = []
    for profile in profiles:
        if not os.path.isdir(profile):
            continue
        for sub in CHROMIUM_CACHE_DIRS:
            path = os.path.join(profile, sub)
            if os.path.isdir(path):
                paths.append(path)
    return paths


def _store_caches(local: str) -> list[str]:
    """Store / UWP 应用的本地缓存。

    `LocalState` 才是应用数据，`LocalCache` 与 `TempState` 按微软的约定是
    可丢弃的缓存，删掉应用会自己重建。
    """
    paths: list[str] = []
    for package in _children(os.path.join(local, "Packages")):
        for sub in ("LocalCache", "TempState"):
            path = os.path.join(package, sub)
            if os.path.isdir(path):
                paths.append(path)
    return paths


# Firefox 的缓存目录。**不能整目录扫 Profiles** —— 那里面是 places.sqlite
# （书签+历史）、logins.json（密码）、cookies.sqlite，按「3 天前的文件」一刀切
# 会把它们全收走，而且删除直接进回收站，用户很难意识到丢了什么。
FIREFOX_CACHE_DIRS = ("cache2", "startupCache", "shader-cache", "thumbnails", "OfflineCache")


def _firefox_caches(local: str) -> list[str]:
    """Firefox 每个配置目录下的缓存子目录（新版缓存放在 LOCALAPPDATA）。"""
    profiles = os.path.join(local, r"Mozilla\Firefox\Profiles")
    paths: list[str] = []
    for profile in _children(profiles):
        for sub in FIREFOX_CACHE_DIRS:
            path = os.path.join(profile, sub)
            if os.path.isdir(path):
                paths.append(path)
    return paths


@lru_cache(maxsize=1)
def builtin_rules() -> tuple[Rule, ...]:
    """内置规则：只覆盖低风险、可再生的目录。

    注册表、卸载残留、系统更新缓存这些风险高的先不放进来 —— 等安全层跑够
    一段时间、用户有信任基础了再逐步开放。

    结果缓存：规则只依赖环境变量，进程内不会变。而 `rule_summary()` 每渲染
    一次列表就调一次，社交缓存那几条还要探目录，不缓存等于白扫一遍盘。
    """
    rules: list[Rule] = []

    temp = _env("TEMP") or _env("TMP")
    if temp:
        rules.append(
            Rule(
                "user_temp",
                "用户临时文件",
                "临时文件",
                [temp],
                # 只留 7 天等于放走大部分垃圾：正常用一天就能攒下上 GB。
                # 1 天前的临时文件基本已经没程序在用了，删不掉的会被安全层跳过。
                1,
                True,
                note="1 天前的临时文件",
            )
        )

    local = _env("LOCALAPPDATA")
    if local:
        rules.append(
            Rule(
                "crash_dumps",
                "程序崩溃转储",
                "临时文件",
                [os.path.join(local, "CrashDumps")],
                0,
                True,
                note="程序崩溃时写下的内存转储",
            )
        )
        for rule_id, name, user_data in (
            ("edge_cache", "Edge 缓存", r"Microsoft\Edge\User Data"),
            ("chrome_cache", "Chrome 缓存", r"Google\Chrome\User Data"),
            ("brave_cache", "Brave 缓存", r"BraveSoftware\Brave-Browser\User Data"),
        ):
            cache_paths = _chromium_caches(local, user_data)
            if cache_paths:
                rules.append(
                    Rule(
                        rule_id,
                        name,
                        "浏览器缓存",
                        cache_paths,
                        3,
                        True,
                        note="网页缓存与代码缓存，不影响登录状态和书签",
                    )
                )

        store_cache = _store_caches(local)
        if store_cache:
            rules.append(
                Rule(
                    "store_cache",
                    "应用商店缓存",
                    "应用缓存",
                    store_cache,
                    3,
                    True,
                    note="UWP/Store 应用的临时缓存，应用会自行重建",
                )
            )

        ff_cache = _firefox_caches(local)
        if ff_cache:
            rules.append(
                Rule(
                    "firefox_cache",
                    "Firefox 缓存",
                    "浏览器缓存",
                    ff_cache,
                    3,
                    True,
                    note="只清 cache2 / startupCache 等缓存目录，不碰书签与密码",
                )
            )

        inet_cache = os.path.join(local, r"Microsoft\Windows\INetCache")
        if os.path.isdir(inet_cache):
            rules.append(
                Rule(
                    "inet_cache",
                    "系统网页缓存",
                    "系统缓存",
                    [inet_cache],
                    3,
                    True,
                    note="旧版组件与系统内嵌网页的缓存",
                )
            )

        # 显卡着色器缓存：玩游戏、跑 3D 应用之后能攒到几百 MB 甚至几个 GB。
        # 删掉只会让下次启动时重新编译着色器，不影响任何设置。
        shader_paths = [
            path
            for path in (
                os.path.join(local, "D3DSCache"),
                os.path.join(local, r"NVIDIA\DXCache"),
                os.path.join(local, r"NVIDIA\GLCache"),
                os.path.join(local, r"AMD\DxCache"),
                os.path.join(local, r"AMD\GLCache"),
            )
            if os.path.isdir(path)
        ]
        if shader_paths:
            rules.append(
                Rule(
                    "shader_cache",
                    "显卡着色器缓存",
                    "系统缓存",
                    shader_paths,
                    3,
                    True,
                    note="下次启动游戏时会自动重新编译",
                )
            )


        explorer = os.path.join(local, r"Microsoft\Windows\Explorer")
        if os.path.isdir(explorer):
            rules.append(
                Rule(
                    "thumbnails",
                    "缩略图缓存",
                    "系统缓存",
                    [explorer],
                    3,
                    False,
                    note="只清理 thumbcache_ / iconcache_ 数据库",
                )
            )

    windir = _env("WINDIR")
    if windir:
        windows_temp = os.path.join(windir, "Temp")
        if os.path.isdir(windows_temp):
            rules.append(
                Rule(
                    "windows_temp",
                    "系统临时文件",
                    "临时文件",
                    [windows_temp],
                    3,
                    True,
                    allow_prefixes=[windows_temp],
                    note="系统级临时目录，3 天前的文件",
                    needs_admin=True,
                )
            )
        logs = os.path.join(windir, "Logs")
        if os.path.isdir(logs):
            rules.append(
                Rule(
                    "windows_logs",
                    "系统日志",
                    "系统缓存",
                    [logs],
                    7,
                    True,
                    allow_prefixes=[logs],
                    note="Windows 组件写下的日志",
                )
            )
        prefetch = os.path.join(windir, "Prefetch")
        if os.path.isdir(prefetch):
            rules.append(
                Rule(
                    "prefetch",
                    "预读取缓存",
                    "系统缓存",
                    [prefetch],
                    7,
                    True,
                    allow_prefixes=[prefetch],
                    note="程序启动预热记录，会自行重建",
                )
            )
        update_dl = os.path.join(windir, "SoftwareDistribution", "Download")
        if os.path.isdir(update_dl):
            rules.append(
                Rule(
                    "windows_update_download",
                    "系统更新缓存",
                    "更新残留",
                    [update_dl],
                    7,
                    True,
                    allow_prefixes=[update_dl],
                    note="已安装更新的安装包残留",
                    needs_admin=True,
                )
            )
        delivery = os.path.join(
            windir,
            "ServiceProfiles",
            "NetworkService",
            "AppData",
            "Local",
            "Microsoft",
            "Windows",
            "DeliveryOptimization",
        )
        if os.path.isdir(delivery):
            rules.append(
                Rule(
                    "delivery_optimization",
                    "传递优化缓存",
                    "更新残留",
                    [delivery],
                    7,
                    True,
                    allow_prefixes=[delivery],
                    note="更新分发的点对点缓存",
                    needs_admin=True,
                )
            )

    # ---------------------------------------------------------- 系统残留
    #
    # 这几处是 C 盘上「用户看不见、但经常占几个 GB」的地方，删掉系统会重建
    # 或本来就不再需要。全部在 Windows 目录下，必须显式给 allow_prefixes。
    #
    # 盘根下的东西（`C:\$WinREAgent`、Windows.old 之类）不在这里做：
    # safety 对「根目录的直接子项」一律判保护，allow_prefixes 也放不开。
    # 那条防线是给 pagefile.sys / bootmgr 兜底的，不为了几 GB 去动它。
    leftovers = (
        (
            "live_kernel_reports",
            "内核转储报告",
            os.path.join(windir, "LiveKernelReports") if windir else "",
            0,
            "显卡/驱动异常时写下的诊断转储",
        ),
        (
            "minidump",
            "蓝屏转储",
            os.path.join(windir, "Minidump") if windir else "",
            0,
            "蓝屏时保存的小型内存转储",
        ),
        (
            "windows_panther",
            "安装日志",
            os.path.join(windir, "Panther") if windir else "",
            7,
            "系统安装与升级过程写下的日志",
        ),
        (
            "windows_system_temp",
            "系统临时目录",
            os.path.join(windir, "SystemTemp") if windir else "",
            3,
            "Win11 24H2 起的独立系统临时目录",
        ),
    )
    for rule_id, name, path, age, note in leftovers:
        if path and os.path.isdir(path):
            rules.append(
                Rule(
                    rule_id,
                    name,
                    "系统残留",
                    [path],
                    age,
                    True,
                    allow_prefixes=[path],
                    note=note,
                    needs_admin=True,
                )
            )

    # ---------------------------------------------------------- 开发缓存
    #
    # 开发机上是几十 GB 级别。默认不勾：删了要重新下载，联网慢的时候很痛，
    # 让用户自己判断。
    if local:
        dev_caches: list[str] = []
        for sub in (
            r"npm-cache\_cacache",
            r"pip\Cache",
            r"Yarn\Cache",
            r"pnpm\store",
        ):
            path = os.path.join(local, sub)
            if os.path.isdir(path):
                dev_caches.append(path)
        if dev_caches:
            rules.append(
                Rule(
                    "dev_cache",
                    "开发工具缓存",
                    "开发缓存",
                    dev_caches,
                    7,
                    True,
                    note="npm / pip / Yarn / pnpm 的下载缓存，删后需要重新下载",
                    default_off=True,
                )
            )


    # 社交软件缓存**不在这里扫**。
    #
    # 微信/QQ/企业微信/钉钉的缓存原来在这四行里各有一条规则，而「软件专清」
    # 整页做的就是同一件事：同一批目录，两个页面都能清、都能报体积，数字还
    # 按不同口径算（这边按目录类型，那边按软件聚合）。用户在 A 页清完，
    # 切到 B 页还显示同样的 GB，只会得出「没清掉」的结论。
    #
    # 现在由 core/appclean.py 单独负责，体检也从那边读（store.AppCleanState）。

    program_data = _env("ProgramData") or _env("ALLUSERSPROFILE")
    if program_data:
        wer = os.path.join(program_data, "Microsoft", "Windows", "WER")
        if os.path.isdir(wer):
            rules.append(
                Rule(
                    "wer_reports",
                    "错误报告存档",
                    "系统缓存",
                    [wer],
                    0,
                    True,
                    allow_prefixes=[wer],
                    note="应用崩溃后上报的存档文件",
                    needs_admin=True,
                )
            )

    return tuple(rules)


def update_backlog(items: list[FileItem]) -> int:
    """更新残留的体积，单独统计给体检用。"""
    return sum(i.size for i in items if i.rule_id in UPDATE_RULE_IDS)


# ---------------------------------------------------------------- 过滤


def _is_risky_windows_path(path: str) -> bool:
    if not safety.is_under_system_windows(path):
        return False
    lowered = path.lower().replace("/", "\\")
    for sub in WINDOWS_RISKY_SUBPATHS:
        if sub.startswith("\\"):
            if sub in lowered:
                return True
        elif ("\\" + sub + "\\") in lowered or lowered.endswith("\\" + sub):
            return True
    return False


def _can_open(path: str) -> bool:
    """能否以只读打开。打不开说明被占用，预判删不掉。"""
    try:
        with open(path, "rb"):
            return True
    except OSError:
        return False


def _accept(path: str, name: str, rule: Rule, age_days: float) -> bool:
    lowered = name.lower()

    if lowered in SYSTEM_MARKER_FILES:
        return False
    # 缩略图规则只收 thumbcache_*/iconcache_*.db：Explorer 目录下还有很多
    # 数据库文件（比如 `iconcache_*` 的兄弟 `thumbcache_idx.db`），不能整目录收
    if rule.id == "thumbnails" and not lowered.startswith(("thumbcache_", "iconcache_")):
        return False
    if lowered.endswith(".lnk"):
        return False
    if rule.max_age_days and age_days < rule.max_age_days:
        return False
    if _is_risky_windows_path(path):
        return False
    if Path(path).suffix.lower() in RISKY_EXT and safety.is_under_system_windows(path):
        return False
    if lowered.endswith(LOCK_PRONE_SUFFIX) and not _can_open(path):
        return False
    # 这里刻意不再逐文件调 safety.is_protected()：它内部要做 realpath，
    # 对每个文件都解析一遍符号链接，几千个文件就是几十秒。
    # 保护判定已经上移到「每条规则的根目录」做一次（见 scan），
    # 根目录不在保护区，子文件就不可能被保护；删除前 delete() 还会再拦一次。
    return True


# ---------------------------------------------------------------- 扫描


def scan(
    rule_ids: list[str] | None = None,
    should_stop=lambda: False,
    on_progress=None,
):
    """流式扫描，逐批 yield `list[FileItem]`。

    串行遍历每条规则：并发扫盘会让磁盘 IO 争用，反而更慢，而且没法及时响应取消。
    """
    rules = [r for r in builtin_rules() if not rule_ids or r.id in rule_ids]
    now = time.time()

    for rule in rules:
        if should_stop():
            return
        batch: list[FileItem] = []
        seen = 0

        for root in rule.paths:
            if should_stop():
                return
            if not os.path.isdir(root):
                continue
            # 保护判定只做一次：根目录本身受保护就整条规则跳过。
            # 逐文件做 realpath 会把扫描拖慢几十倍（见 _accept 里的说明）。
            if safety.is_protected(root, rule.allow_prefixes):
                continue

            if rule.recursive:
                walker = os.walk(root, topdown=True, onerror=lambda _e: None)
            else:
                walker = [(root, [], [e.name for e in _safe_scandir(root)])]

            for dirpath, _dirnames, filenames in walker:
                if should_stop():
                    return
                for filename in filenames:
                    if seen >= MAX_FILES_PER_RULE:
                        break
                    full = os.path.join(dirpath, filename)
                    try:
                        stat = os.stat(full)
                    except OSError:
                        continue
                    seen += 1
                    age_days = (now - stat.st_mtime) / 86400
                    if not _accept(full, filename, rule, age_days):
                        continue
                    batch.append(FileItem(full, stat.st_size, rule.id, rule.category))
                    if len(batch) >= BATCH:
                        if on_progress:
                            on_progress(rule.id, len(batch))
                        yield batch
                        batch = []

        if batch:
            if on_progress:
                on_progress(rule.id, len(batch))
            yield batch


def _safe_scandir(path: str):
    try:
        return [e for e in os.scandir(path) if e.is_file()]
    except OSError:
        return []


# ---------------------------------------------------------------- 删除


def _allow_index() -> dict[str, tuple[str, ...]]:
    """rule_id → 这条规则显式放行的系统目录。

    扫描时是「按规则放行」的（`is_protected(root, rule.allow_prefixes)`），
    删除时必须用同一份放行清单。否则 `C:\\Windows\\Temp`、`SoftwareDistribution
    \\Download` 这些系统级规则的每个文件都会被安全层拦下 —— 表现出来就是
    「已清理 0 个 · 133 个被占用」，而且这些项永远留在清单里，重试多少次都一样。
    """
    try:
        rules = builtin_rules()
    except Exception:
        return {}
    return {r.id: tuple(r.allow_prefixes) for r in rules if r.allow_prefixes}


def delete_items(
    items: list[FileItem],
    should_stop=lambda: False,
    on_progress=None,
) -> tuple[int, int, int, list[FileItem]]:
    """删除文件，返回 (成功数, 失败数, 释放字节, 没删掉的项)。

    「没删掉的项」既包含被占用/受保护而失败的，也包含用户中途取消后没轮到的。
    调用方把它们写回 store，列表里剩下的就是真实还剩的垃圾，不会凭空变少。

    逐项再过一遍安全层 —— 扫描到删除之间可能隔了几分钟，期间路径可能变成
    符号链接指向别处，多检查一次不吃亏。
    """
    deleted = failed = 0
    freed = 0
    remaining: list[FileItem] = []
    total = len(items)
    # 只有真的碰到系统目录里的项才去建规则索引：builtin_rules() 要探测社交缓存
    # 目录，多数清理用不上，不值得每次删除都付这份开销
    allow: dict[str, tuple[str, ...]] | None = None

    for index, item in enumerate(items):
        if should_stop():
            remaining.extend(items[index:])
            break

        # 文件已经不在了（上一轮删掉、或被别的程序清过）。这种项留在 remaining
        # 里会变成永远删不掉的僵尸：点多少次「重试」都还在，数字一点不变。
        # 算「已清理」，但不加 freed —— 这次删除并没有真的腾出它那点空间。
        if not os.path.lexists(item.path):
            deleted += 1
            if on_progress and (index % 50 == 0 or index == total - 1):
                on_progress(index + 1, total)
            continue

        try:
            if allow is None:
                allow = _allow_index()
            if safety.is_protected(item.path, allow.get(item.rule_id)):
                failed += 1
                remaining.append(item)
            else:
                os.remove(item.path)
                deleted += 1
                freed += item.size
        except OSError:
            failed += 1
            remaining.append(item)

        if on_progress and (index % 50 == 0 or index == total - 1):
            on_progress(index + 1, total)

    return deleted, failed, freed, remaining


def rule_summary(items: list[FileItem]) -> list[RuleResult]:
    """按规则汇总，给界面显示用。"""
    names = {r.id: (r.name, r.category) for r in builtin_rules()}
    grouped: dict[str, RuleResult] = {}
    for item in items:
        name, category = names.get(item.rule_id, (item.rule_id, item.category))
        result = grouped.setdefault(
            item.rule_id, RuleResult(item.rule_id, name, category)
        )
        result.count += 1
        result.size += item.size
    return sorted(grouped.values(), key=lambda r: r.size, reverse=True)
