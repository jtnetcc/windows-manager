"""软件专清：按「软件」维度清理缓存与已接收文件。

和 `cleaner` 里那条「社交缓存」规则的分工：

- `cleaner` 是按**规则**扫全盘，社交缓存只是其中一条，粗粒度、一次清光；
- `appclean` 是按**软件**聚合，一个软件拆成图片 / 视频 / 文件 / 表情 / 缓存，
  用户可以只清其中几类，也能看清每个软件到底吃了多少空间。

两条硬规则沿用 `social.py`：

1. **白名单子目录**，不是黑名单。只清理明确列出的目录名，
   聊天记录数据库（`Msg`、`msg`、`Msg3.0.db` 之类）永远不在名单里。
2. **必须存在账号目录**才认为装了这款软件 —— 卸载后经常残留一个空壳根目录。

微信 4.x 的 `xwechat_files` 目前只认 `FileStorage` 结构。新版客户端把附件
挪到了 `attach/`，结构没验证过之前不碰：宁可少清几个 GB，也不能误删聊天记录。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


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


def _documents() -> str:
    return os.path.join(os.path.expanduser("~"), "Documents")


def _specs() -> list[_Spec]:
    """每次现算：环境变量（APPDATA 等）在进程内不会变，但路径拼接很便宜。"""
    docs = _documents()
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


def discover() -> list[App]:
    """扫描本机装了哪些软件，返回有可清理目录的那些。

    一个可清理目录都没找到的软件不出现 —— 列一行「微信 0 KB」没有意义。
    """
    apps: list[App] = []
    for spec in _specs():
        targets: dict[str, Target] = {}
        for root in spec.roots:
            if not os.path.isdir(root):
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
        stats.append(AppStat(app, items))

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
