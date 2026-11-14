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
        for rule_id, name, sub in (
            ("edge_cache", "Edge 缓存", r"Microsoft\Edge\User Data\Default\Cache"),
            ("chrome_cache", "Chrome 缓存", r"Google\Chrome\User Data\Default\Cache"),
        ):
            path = os.path.join(local, sub)
            if os.path.isdir(path):
                rules.append(
                    Rule(
                        rule_id,
                        name,
                        "浏览器缓存",
                        [path],
                        3,
                        True,
                        note="网页缓存，不影响登录状态",
                    )
                )

        firefox = os.path.join(local, r"Mozilla\Firefox\Profiles")
        if os.path.isdir(firefox):
            rules.append(
                Rule(
                    "firefox_cache",
                    "Firefox 缓存",
                    "浏览器缓存",
                    [firefox],
                    3,
                    True,
                    note="网页缓存，不影响登录状态",
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
                    note="只清理 thumbcache_*.db",
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

    # 社交软件缓存：只清图片/视频/文件缓存，不碰聊天记录数据库。
    # 这些删掉的是用户实际收到过的内容，所以默认不勾选。
    try:
        from . import social

        for item in social.discover():
            rules.append(
                Rule(
                    item.id,
                    item.name,
                    "社交缓存",
                    item.paths,
                    0,
                    True,
                    note="只清缓存，不动聊天记录",
                    default_off=True,
                )
            )
    except Exception:
        applog.silenced("cleaner.builtin_rules")

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
    # 缩略图规则只收 thumbcache_*.db
    if rule.id == "thumbnails" and not lowered.startswith("thumbcache_"):
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
