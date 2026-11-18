"""软件专清：按「软件」维度清理缓存与已接收文件。

社交软件缓存**只在这里处理**。`cleaner` 原来也有四条同样的规则，两页能对
同一批目录给出不同数字，已在 v0.0.40 删除（见交接文档 6.38）。

- `cleaner` 是按**规则**扫全盘，粗粒度、一次清光；
- `appclean` 是按**软件**聚合，一个软件拆成图片 / 视频 / 文件 / 表情 / 缓存，
  用户可以只清其中几类，也能看清每个软件到底吃了多少空间。

两条硬规则：

1. **白名单子目录**，不是黑名单。只清理明确列出的目录名，
   聊天记录数据库（`Msg`、`msg`、`Msg3.0.db` 之类）永远不在名单里。
2. **必须存在账号目录**才认为装了这款软件 —— 卸载后经常残留一个空壳根目录。

微信 4.x 的 `xwechat_files` 目前只认 `FileStorage` 结构。新版客户端把附件
挪到了 `attach/`，结构没验证过之前不碰：宁可少清几个 GB，也不能误删聊天记录。
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field

from . import winpath


@dataclass
class Target:
    """一类可清理的内容。多个账号目录会合并到同一个 target 里。"""

    key: str
    name: str
    kind: str
    note: str
    order: int = 0
    paths: list[str] = field(default_factory=list)


@dataclass
class App:
    key: str
    name: str
    hint: str
    targets: list[Target] = field(default_factory=list)


@dataclass
class TargetStat:
    target: Target
    size: int = 0
    count: int = 0


@dataclass
class AppStat:
    app: App
    items: list[TargetStat] = field(default_factory=list)

    @property
    def size(self) -> int:
        return sum(item.size for item in self.items)

    @property
    def count(self) -> int:
        return sum(item.count for item in self.items)

    @property
    def kinds(self) -> int:
        return sum(1 for item in self.items if item.count)


# ---------------------------------------------------------------- 软件清单


@dataclass(frozen=True)
class _Kind:
    key: str
    name: str
    kind: str
    note: str
    subdir: str


@dataclass(frozen=True)
class _Spec:
    key: str
    name: str
    hint: str
    roots: tuple[str, ...]
    kinds: tuple[_Kind, ...]


def _specs() -> list[_Spec]:
    """每次现算：环境变量（APPDATA 等）在进程内不会变，但路径拼接很便宜。"""
    docs = winpath.documents()
    appdata = os.environ.get("APPDATA", "")
    local = os.environ.get("LOCALAPPDATA", "")

    specs = [
        _Spec(
            "wechat",
            "微信",
            "聊天记录数据库不动，只清图片视频等缓存",
            (
                os.path.join(docs, "WeChat Files"),
                os.path.join(docs, "xwechat_files"),
            ),
            (
                _Kind("image", "聊天图片", "image",
                      "聊天里看过的图片，删了会重新下载", r"FileStorage\Image"),
                _Kind("video", "聊天视频", "video",
                      "聊天里看过的视频，删了会重新下载", r"FileStorage\Video"),
                _Kind("file", "聊天文件", "file",
                      "聊天里收到的文件，重要文件请先另存", r"FileStorage\File"),
                _Kind("emoji", "表情包", "emoji",
                      "收藏的自定义表情，删了需要重新收藏", r"FileStorage\CustomEmotion"),
                _Kind("attach", "聊天附件", "image",
                      "图片视频的缩略图与附件缓存", r"FileStorage\MsgAttach"),
                _Kind("cache", "程序缓存", "cache",
                      "网页缓存、小程序缓存，不影响登录", r"FileStorage\Cache"),
            ),
        ),
        _Spec(
            "qq",
            "QQ",
            "只清接收的图片视频，聊天记录不动",
            (os.path.join(docs, "Tencent Files"),),
            (
                _Kind("image", "聊天图片", "image",
                      "聊天里看过的图片", "Image"),
                _Kind("video", "聊天视频", "video",
                      "聊天里看过的视频", "Video"),
                _Kind("file", "接收文件", "file",
                      "好友发来的文件，重要文件请先另存", "FileRecv"),
                _Kind("audio", "语音消息", "file",
                      "语音消息缓存", "Audio"),
            ),
        ),
        _Spec(
            "wxwork",
            "企业微信",
            "同事发来的图片视频与文件缓存",
            (os.path.join(docs, "WXWork"),),
            (
                _Kind("image", "聊天图片", "image",
                      "会话里的图片缓存", "Image"),
                _Kind("video", "聊天视频", "video",
                      "会话里的视频缓存", "Video"),
                _Kind("file", "聊天文件", "file",
                      "会话里收到的文件，重要文件请先另存", "File"),
                _Kind("global_image", "公共图片", "image",
                      "企业通讯录与公告里的图片", r"Global\Image"),
                _Kind("global_file", "公共文件", "file",
                      "企业共享文件缓存", r"Global\File"),
                _Kind("cache", "程序缓存", "cache",
                      "不影响登录与聊天记录", "Cache"),
            ),
        ),
        _Spec(
            "dingtalk",
            "钉钉",
            "会话图片视频与文件缓存",
            tuple(
                path
                for path in (
                    os.path.join(appdata, "DingTalk") if appdata else "",
                    os.path.join(local, "DingTalk") if local else "",
                )
                if path
            ),
            (
                _Kind("image", "聊天图片", "image", "会话里的图片缓存", "Image"),
                _Kind("video", "聊天视频", "video", "会话里的视频缓存", "Video"),
                _Kind("file", "聊天文件", "file",
                      "会话里收到的文件，重要文件请先另存", "File"),
                _Kind("cache", "程序缓存", "cache", "不影响登录", "Cache"),
            ),
        ),
    ]

    chrome_kinds = (
        _Kind("cache", "网页缓存", "cache",
              "网页资源缓存，不影响登录状态", "Cache"),
        _Kind("code", "脚本缓存", "cache",
              "网页脚本编译缓存，会自动重建", "Code Cache"),
        _Kind("gpu", "显卡缓存", "cache",
              "GPU 着色器缓存，会自动重建", "GPUCache"),
        _Kind("media", "媒体缓存", "video",
              "音视频播放缓存", "Media Cache"),
        _Kind("sw", "离线数据", "cache",
              "网页离线数据，不影响账号", r"Service Worker\CacheStorage"),
    )

    if local:
        specs.append(
            _Spec(
                "chrome",
                "Chrome",
                "网页缓存与离线数据，不影响账号与书签",
                (os.path.join(local, r"Google\Chrome\User Data"),),
                chrome_kinds,
            )
        )
        specs.append(
            _Spec(
                "edge",
                "Edge",
                "网页缓存与离线数据，不影响账号与收藏",
                (os.path.join(local, r"Microsoft\Edge\User Data"),),
                chrome_kinds,
            )
        )
        specs.append(
            _Spec(
                "firefox",
                "Firefox",
                "网页缓存，不影响账号与书签",
                (os.path.join(local, r"Mozilla\Firefox\Profiles"),),
                (
                    _Kind("cache", "网页缓存", "cache",
                          "网页资源缓存，会自动重建", "cache2"),
                ),
            )
        )
    return specs


# Chrome 的账号目录是 Default / Profile 1 / Profile 2…，别把 Crashpad 之类算进去
_SKIP_ACCOUNTS = {"crashpad", "shadercache", "grshadercache"}


def _account_dirs(root: str) -> list[str]:
    try:
        return [
            entry.path
            for entry in os.scandir(root)
            if entry.is_dir() and entry.name.lower() not in _SKIP_ACCOUNTS
        ]
    except OSError:
        return []



# ---------------------------------------------------------------- 缓存目录定义

# 目录名 → 类目。只有出现在这张表里的名字才会成为可清理目标，表外一律不碰
# —— 宁可少清，不能误删。
# 分三类是因为代价不一样：缓存删了自动重建；网页离线数据要重新联网加载；
# 日志删了不影响使用，但以后没法拿它排查问题，所以单独一类、单独一句话说清。
_CACHE_KINDS: dict[str, str] = {}
for _name in (
    "cache",
    "code cache",
    "gpucache",
    "dawncache",
    "grshadercache",
    "shadercache",
    "cache_data",
    "cacheddata",
    "localcache",
    "tempstate",
    "imagecache",
    "thumbnails",
):
    _CACHE_KINDS[_name] = "cache"
for _name in ("cachestorage", "service worker", "blob_storage"):
    _CACHE_KINDS[_name] = "web"
for _name in ("logs", "log", "crashpad", "crashes", "crashreports"):
    _CACHE_KINDS[_name] = "logs"

_CACHE_DIR_NAMES = set(_CACHE_KINDS)

_KIND_META = {
    "cache": ("程序缓存", "软件自己的缓存，删掉会自动重建"),
    "web": ("网页离线数据", "网页与小程序的离线缓存，删掉要重新联网加载"),
    "logs": ("日志与转储", "日志和崩溃转储，删掉不影响使用，但以后没法用它排查问题"),
}
_KIND_ORDER = ("cache", "web", "logs")

# 看着像缓存、实际装着用户数据的名字，永远不进清单
_NEVER = {"cache2", "user data", "local storage", "session storage", "indexeddb"}

# 父目录是这些的话，下面的 `cache` 是**插件本体**而不是缓存：
# `.workbuddy\plugins\cache` 里躺着的是已装插件解压好的内容（实测 119 MB），
# 删掉插件就消失了，还得联网重装。名字像缓存不等于就是缓存。
_PLUGIN_HOSTS = {"plugins", "extensions", "addons", "modules"}

# 数据根下的这些是系统或开发工具目录，不属于「装了某个软件」。
# `tempstate` 在这里出现是顶层特判：顶层一个孤零零的 TempState 是系统残留，
# 而软件内部的 `TempState` 仍然算缓存（那张表在 `_CACHE_KINDS`）。
_SYSTEM_DIRS = {
    "temp",
    "packages",
    "programs",
    "programsdata",
    "tempstate",
    "crashdumps",
    "d3dscache",
    "pip",
    "fontconfig",
    "connecteddevicesplatform",
    "comms",
    "publishercache",
    "virtualstore",
    "application data",
    "history",
    "icons",
    "tiles",
    "elevateddiagnostics",
    "subinacl",
    "microsoftedgeupdate",
    "turingshield",
    "tbilsis",
    "node-addon-native-custom-loader",
}

# 厂商名。拿它当软件名毫无信息量（「Tencent」到底指哪个产品？），所以这里
# 强制再下钻一层看产品。名单刻意短：只收「几乎必然是厂商壳」的那几个。
_VENDOR_DIRS = {
    "google",
    "microsoft",
    "mozilla",
    "tencent",
    "netease",
    "adobe",
    "apple",
    "jetbrains",
    "amazon",
    "samsung",
    "huawei",
    "xiaomi",
    "alibaba",
    "baidu",
    "bytedance",
    "kingsoft",
    "qihoo",
    "360",
    "sogou",
    "thunder",
    "yy",
}

# 厂商目录里的这些不算软件：系统组件、更新器、崩溃收集，清了也没意义
_VENDOR_SKIP = {
    "windows",
    "installer",
    "office16",
    "identitycrl",
    "internet explorer",
    "edgeupdate",
    "edgecore",
    "windows defender",
    "vault",
    "updater",
    "update",
    "googleupdater",
    "crashpad",
    "squirrel",
    "squirreltemp",
    "elevation_service",
    "crashreportclient",
    "common",
    "cache",
    "logs",
    "crashreports",
}

# 目录名以这些尾巴结尾的也不是软件：更新器、扩展、崩溃上报器
_NOISE_SUFFIX = (
    "updater",
    "extension",
    "helper",
    "crashpad",
    "crashreporter",
    "elevation_service",
)

# 目录名 → 显示名。纯标签，不参与「列不列」的判断：认不认得出软件，只看
# 它下面有没有白名单里的缓存目录。
_DISPLAY_NAMES = {
    "wechat": "微信",
    "wxwork": "企业微信",
    "dingtalk": "钉钉",
    "workbuddy": "WorkBuddy",
    "workbuddyai": "WorkBuddy AI",
    "codebuddy": "CodeBuddy",
    "qoder": "Qoder",
    "trae": "Trae",
    "windsurf": "Windsurf",
    "cloudmusic": "网易云音乐",
    "neteasecloudmusic": "网易云音乐",
    "baidunetdisk": "百度网盘",
    "aliyundrive": "阿里云盘",
    "thunder": "迅雷",
    "leidian": "雷电模拟器",
    "leidian14": "雷电模拟器",
    "microsoftvscode": "VS Code",
    "vscode": "VS Code",
    "cursor": "Cursor",
    "obsstudio": "OBS Studio",
    "telegramdesktop": "Telegram",
    "epicgameslauncher": "Epic Games",
    "pycharm": "PyCharm",
    "idea": "IntelliJ IDEA",
    "discord": "Discord",
    "spotify": "Spotify",
}

_TRAILING_NOISE = re.compile(
    r"(?:[-_ ](?:updater|desktop|client|extension|native|app))+$", re.I
)
_VERSION_TEXT = re.compile(r"\b\d+(?:\.\d+)+\b")
_WORD_JOIN = re.compile(r"(?<=[A-Za-z])[-_](?=[A-Za-z])")

# 列太多就是另一种噪音。个人机上正常也就十来个有缓存的软件。
_MAX_APPS = 40
# 自适应识别出的软件，总量低于这个值就不列：几十 KB 的碎屑清了也腾不出空间
_MIN_AUTO_SIZE = 1024 * 1024
# 发现阶段的时间上限。慢盘上宁可少列两个，也不能让界面卡住。
_DISCOVER_BUDGET = 6.0


@dataclass
class DiscoveryInfo:
    """一次发现过程的统计，界面用它解释「为什么只有这几个」。"""

    roots: int = 0          # 扫了几个数据根
    probes: int = 0         # 看了几个顶层目录
    with_cache: int = 0     # 其中认出了可清缓存的
    skipped: list[str] = field(default_factory=list)   # 有数据目录但没有缓存


_LAST_INFO = DiscoveryInfo()


def discovery_info() -> DiscoveryInfo:
    """上一次 `discover()` 的统计。界面拿它写底部那行说明。"""
    return _LAST_INFO


def _subdirs(path: str, limit: int = 400) -> list[str]:
    out: list[str] = []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if len(out) >= limit:
                    break
                try:
                    if entry.is_dir(follow_symlinks=False):
                        out.append(entry.path)
                except OSError:
                    continue
    except OSError:
        pass
    return out


def _cache_dirs_under(root: str, max_depth: int = 4, budget: int = 1500) -> list[str]:
    """在 root 下按名字白名单找可清目录，最多四层。

    四层不是拍脑袋：`Vendor\\Product\\User Data\\Default\\Cache` 正好四层，
    Electron 系几乎全是这个结构。只扫一层的话，除了写死的那几个软件什么都
    看不见 —— 这正是「按软件清理只列出三个软件」的根因。

    `User Data`、`Local Storage` 这些名字本身绝不当缓存（走 `_NEVER`），但
    **必须继续往下钻**：缓存就藏在它们下面，见到就 return 等于什么都找不到。
    """
    found: list[str] = []
    stack: list[tuple[str, int]] = [(root, 0)]
    seen = 0
    while stack and seen < budget:
        current, level = stack.pop()
        for child in _subdirs(current, limit=250):
            seen += 1
            if seen >= budget:
                break
            low = os.path.basename(child).lower()
            if low in _CACHE_DIR_NAMES:
                host = os.path.basename(os.path.dirname(child)).lower()
                if host in _PLUGIN_HOSTS:
                    continue      # `plugins\cache` 装的是插件本体，不是缓存
                found.append(child)
                continue          # 缓存目录里面不会再套一层缓存
            if level + 1 < max_depth:
                stack.append((child, level + 1))
    return found


def _touches(path: str, roots: list[str]) -> bool:
    """这个目录跟已有精细规则是否重叠（互为父子都算）。

    微信 / QQ / Chrome 这些有专门的类目划分，自动识别再列一遍就成了两份
    指向同一批文件的条目：同屏两个「微信」，删哪个都像没删。
    """
    probe = os.path.normcase(path)
    for root in roots:
        other = os.path.normcase(root)
        if probe == other or probe.startswith(other + os.sep):
            return True
        if other.startswith(probe + os.sep):
            return True
    return False


def _slug(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "_", (text or "").lower()).strip("_") or "app"


def _is_noise(name: str) -> bool:
    """这个目录名是不是「更新器 / 扩展 / 崩溃上报器」这类非软件。"""
    low = os.path.basename((name or "").rstrip("\\/")).lower()
    if low in _SYSTEM_DIRS or low in _VENDOR_SKIP:
        return True
    return any(low.endswith(suffix) for suffix in _NOISE_SUFFIX)


def _pretty_name(raw: str) -> str:
    """把目录名收拾成能看的软件名。

    `brainstorm3-desktop` → `Brainstorm3`、`leidian14` → `雷电模拟器`。
    收拾不出来就原样返回 —— 名字丑一点，也比编一个不存在的软件名强。
    """
    name = (raw or "").strip().lstrip("@.")
    if not name:
        return "未知软件"
    trimmed = _VERSION_TEXT.sub("", _TRAILING_NOISE.sub("", name))
    trimmed = re.sub(r"\s{2,}", " ", _WORD_JOIN.sub(" ", trimmed)).strip(" -_")
    if not trimmed:
        return name
    key = re.sub(r"[^0-9a-z]", "", trimmed.lower())
    hit = _DISPLAY_NAMES.get(key)
    if hit:
        return hit
    if trimmed.isascii() and trimmed.islower():
        return " ".join(word[:1].upper() + word[1:] for word in trimmed.split())
    return trimmed


# ---------------------------------------------------------------- 自适应识别


def _discover_data_dirs(
    spec_roots: list[str],
    spec_names: set[str],
) -> tuple[list[App], DiscoveryInfo]:
    """只从**数据目录**出发识别软件，不看注册表里的已装清单。

    判据只有一条：这个目录下面确实躺着按名字认得出的缓存。目录内容才是
    证据，名字只是标签。所以绿色版、便携版、装在 D 盘的、注册表里压根没
    登记的，都认得出来；反过来，装了却一个缓存都没有的（各种运行库、
    WebView2、驱动）也不会跑进列表里刷存在感 —— 它们本来就没有可清的。

    两层结构要分开：`%APPDATA%\\Tencent\\WeChat` 里 Tencent 只是厂商，
    该显示的是 WeChat。常见厂商强制下钻，其余靠「自身没缓存、但下面有
    多个产品有缓存」自动判定是不是厂商壳。

    `spec_roots` / `spec_names` 用来避开已经写了精细规则的软件，免得同一批
    文件在列表里出现两次。
    """
    info = DiscoveryInfo()
    found: dict[str, dict] = {}
    started = time.monotonic()

    def add(path: str, vendor: str, tag: str, dirs: list[str]) -> None:
        display = _pretty_name(os.path.basename(path))
        if display in spec_names:
            return            # 这款软件已经有更细的规则了
        key = f"{vendor}|{display}".lower()
        slot = found.get(key)
        if slot is None:
            slot = found[key] = {"name": display, "vendor": vendor, "tags": [], "kinds": {}}
        if tag not in slot["tags"]:
            slot["tags"].append(tag)
        for directory in dirs:
            kind = _CACHE_KINDS.get(os.path.basename(directory).lower(), "cache")
            bucket = slot["kinds"].setdefault(kind, [])
            if directory not in bucket:
                bucket.append(directory)

    local = os.environ.get("LOCALAPPDATA", "")
    home = os.path.expanduser("~")
    # 第四项是「只认点目录」开关：家目录里混着桌面、文档、下载，全扫一遍既慢
    # 又没意义。但 `.workbuddy`、`.codebuddy`、`.cursor` 这类 Unix 风格配置
    # 目录才是 Electron 系程序真正存缓存的地方 —— 不扫它们，WorkBuddy 这种
    # 一个字节也认不出来。
    bases = [
        (local, "本机", False),
        (os.environ.get("APPDATA", ""), "本机", False),
    ]
    if local:
        bases.append((os.path.join(local, "Programs"), "程序目录", False))
    if home and os.path.isdir(home):
        bases.append((home, "用户目录", True))

    for base, tag, dot_only in bases:
        if not base or not os.path.isdir(base):
            continue
        info.roots += 1
        for first in _subdirs(base, limit=600):
            if time.monotonic() - started > _DISCOVER_BUDGET:
                break
            low = os.path.basename(first).lower()
            if dot_only and not low.startswith("."):
                continue          # 家目录只认 `.xxx`，桌面文档一律不碰
            if _is_noise(first):
                continue
            if _touches(first, spec_roots):
                continue          # 微信 / QQ / Chrome 这一批有专门规则
            info.probes += 1

            if low in _VENDOR_DIRS:
                for product in _subdirs(first, limit=60):
                    plow = os.path.basename(product).lower()
                    if plow in _VENDOR_SKIP or _touches(product, spec_roots):
                        continue
                    dirs = _cache_dirs_under(product)
                    if dirs:
                        add(product, os.path.basename(first), tag, dirs)
                continue

            dirs = _cache_dirs_under(first)
            if dirs:
                add(first, "", tag, dirs)
                continue

            # 自身没缓存：可能是个不认识的厂商壳，看下一层有没有产品
            products: list[tuple[str, list[str]]] = []
            for product in _subdirs(first, limit=40):
                plow = os.path.basename(product).lower()
                if plow in _VENDOR_SKIP or _touches(product, spec_roots):
                    continue
                sub = _cache_dirs_under(product)
                if sub:
                    products.append((product, sub))
            if len(products) >= 2:
                for product, sub in products:
                    add(product, os.path.basename(first), tag, sub)
            elif products:
                # 只有一个产品：更可能是配置子目录（`Foo\Data\Cache`），
                # 拿外层名字当软件名才对
                add(first, "", tag, products[0][1])
            elif not _is_noise(first):
                info.skipped.append(os.path.basename(first))

    apps: list[App] = []
    for slot in found.values():
        kinds: dict[str, list[str]] = slot["kinds"]
        ordered = [kind for kind in _KIND_ORDER if kind in kinds]
        targets = [
            Target(
                key=f"data_{_slug(slot['vendor'] + '_' + slot['name'])}_{kind}",
                name=_KIND_META[kind][0],
                kind=kind,
                note=_KIND_META[kind][1],
                order=index,
                paths=kinds[kind],
            )
            for index, kind in enumerate(ordered)
        ]
        total = sum(len(paths) for paths in kinds.values())
        prefix = f"{slot['vendor']} · " if slot["vendor"] else ""
        apps.append(
            App(
                key=f"data_{_slug(slot['vendor'] + '_' + slot['name'])}",
                name=slot["name"],
                hint=f"{prefix}{'、'.join(slot['tags'])} · {total} 个可清目录",
                targets=targets,
            )
        )

    apps.sort(key=lambda app: app.name.lower())
    info.with_cache = len(apps)
    return apps[:_MAX_APPS], info


def discover() -> list[App]:
    """扫描本机装了哪些软件，返回有可清目录的那些。

    两条来源，各管一半：

    - **写死规则的**（微信 / QQ / 企业微信 / 钉钉 / Chrome / Edge / Firefox）：
      类目分得细，图片、视频、文件、表情分别能看能选；
    - **自适应识别的**（其余所有软件）：不看注册表清单，只看数据目录本身，
      认出白名单里的缓存就当成一款装了缓存的软件。

    一个可清目录都没找到的软件不出现 —— 列一行「某某 0 KB」没有意义。
    """
    global _LAST_INFO

    apps: list[App] = []
    spec_roots: list[str] = []
    spec_names: set[str] = set()

    for spec in _specs():
        spec_roots.extend(root for root in spec.roots if root)

        targets: dict[str, Target] = {}
        for root in spec.roots:
            if not root or not os.path.isdir(root):
                continue
            # roots 下还有一层「账号 / 配置目录」才算装了：卸载后常留空壳根目录
            accounts = _account_dirs(root)
            if not accounts:
                continue
            for account in accounts:
                for order, kind in enumerate(spec.kinds):
                    full = os.path.join(account, kind.subdir)
                    if not os.path.isdir(full):
                        continue
                    target = targets.setdefault(
                        kind.key,
                        Target(
                            key=f"{spec.key}_{kind.key}",
                            name=kind.name,
                            kind=kind.kind,
                            note=kind.note,
                            order=order,
                        ),
                    )
                    if full not in target.paths:
                        target.paths.append(full)
        if targets:
            apps.append(
                App(
                    spec.key,
                    spec.name,
                    spec.hint,
                    sorted(targets.values(), key=lambda t: t.order),
                )
            )
            spec_names.add(spec.name)

    auto, info = _discover_data_dirs(spec_roots, spec_names)
    apps.extend(auto)
    _LAST_INFO = info
    return apps


# ---------------------------------------------------------------- 扫描


def _measure(paths: list[str], should_stop) -> tuple[int, int]:
    """统计目录下所有文件的总大小与数量。不跟符号链接，避免绕圈。"""
    size = 0
    count = 0
    stack = list(paths)
    while stack:
        if should_stop():
            break
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            size += entry.stat(follow_symlinks=False).st_size
                            count += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return size, count


def scan(should_stop=lambda: False, on_progress=None) -> list[AppStat]:
    """逐软件统计。返回的 AppStat 只保留有内容的类目。"""
    apps = discover()
    total = max(1, len(apps))
    stats: list[AppStat] = []

    for index, app in enumerate(apps):
        if should_stop():
            return stats
        if on_progress:
            on_progress(
                min(99, int(index * 100 / total)),
                f"正在统计 {app.name}…",
            )
        items: list[TargetStat] = []
        for target in app.targets:
            size, count = _measure(target.paths, should_stop)
            if should_stop():
                return stats
            items.append(TargetStat(target, size, count))
        if not any(item.size for item in items):
            # 目录还在、文件已经被清空过：再列一行 0 B 只是噪音
            _LAST_INFO.skipped.append(app.name)
            continue
        total_size = sum(item.size for item in items)
        if app.key.startswith("data_") and total_size < _MIN_AUTO_SIZE:
            # 自适应识别出来的碎屑：几十 KB 的日志清了也腾不出空间，只会把
            # 有分量的几项挤下去
            _LAST_INFO.skipped.append(app.name)
            continue
        stats.append(AppStat(app, [item for item in items if item.size]))

    if on_progress:
        on_progress(100, f"统计完成 · {len(stats)} 个软件")
    return stats


# ---------------------------------------------------------------- 删除


@dataclass
class PurgeResult:
    deleted: int = 0
    failed: int = 0
    freed: int = 0
    remaining: int = 0
    # 被删掉的文件的绝对路径集合，用来把垃圾清理页的旧结果同步掉
    removed_paths: set[str] = field(default_factory=set)


def purge(
    targets: list[Target],
    should_stop=lambda: False,
    on_progress=None,
) -> PurgeResult:
    """清空这些类目下的文件。

    先把文件列出来再交给 `cleaner.delete_items`：删除要过安全层，不能自己
    直接 `os.remove` 掉一个目录。收集阶段只 stat 不读内容，几万个文件的
    开销在几百毫秒量级。
    """
    from . import cleaner

    files: list[cleaner.FileItem] = []
    for target in targets:
        if should_stop():
            break
        for root in target.paths:
            stack = [root]
            while stack:
                if should_stop():
                    break
                current = stack.pop()
                try:
                    with os.scandir(current) as entries:
                        for entry in entries:
                            try:
                                if entry.is_dir(follow_symlinks=False):
                                    stack.append(entry.path)
                                elif entry.is_file(follow_symlinks=False):
                                    files.append(
                                        cleaner.FileItem(
                                            entry.path,
                                            entry.stat(follow_symlinks=False).st_size,
                                            target.key,
                                            target.name,
                                        )
                                    )
                            except OSError:
                                continue
                except OSError:
                    continue

    if should_stop():
        return PurgeResult(remaining=len(files))

    total = max(1, len(files))
    deleted, failed, freed, left = cleaner.delete_items(
        files,
        should_stop=should_stop,
        on_progress=lambda done, _total: (
            on_progress(min(99, int(done * 100 / total)), f"正在删除 {done}/{total}")
            if on_progress
            else None
        ),
    )
    return PurgeResult(
        deleted=deleted,
        failed=failed,
        freed=freed,
        remaining=len(left),
        removed_paths={item.path for item in files} - {item.path for item in left},
    )
