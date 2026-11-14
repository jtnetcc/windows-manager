"""社交软件缓存探测。

只认「图片 / 视频 / 文件缓存」这几类子目录，**永远不碰聊天记录数据库**
（微信的 `Msg`、`msg` 目录，QQ 的 `Msg3.0.db` 之类）。

设计上的两条硬规则：

1. **白名单子目录**，不是黑名单。只清理明确列出的目录名，
   新版本客户端多出来的目录默认不碰。
2. **必须存在账号目录**才认为装了这款软件。只看根目录存在不够 ——
   卸载后经常残留一个空壳根目录。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import winpath


@dataclass
class SocialRule:
    id: str
    name: str
    app: str
    paths: list[str] = field(default_factory=list)


def _children(path: str) -> list[str]:
    try:
        return [
            entry.path
            for entry in os.scandir(path)
            if entry.is_dir() and not entry.name.startswith(".")
        ]
    except OSError:
        return []


# 每款软件：根目录候选 + 账号目录下的缓存子目录白名单
_WECHAT_SUBDIRS = (
    "FileStorage\\Cache",
    "FileStorage\\Image",
    "FileStorage\\Video",
    "FileStorage\\File",
    "FileStorage\\CustomEmotion",
    "FileStorage\\MsgAttach",
)

_QQ_SUBDIRS = (
    "Image",
    "Video",
    "FileRecv",
    "Audio",
)

_WXWORK_SUBDIRS = (
    "Cache",
    "Emotion",
    "Image",
    "Video",
    "File",
    "Global\\Image",
    "Global\\Video",
    "Global\\File",
)

_DINGTALK_SUBDIRS = (
    "Image",
    "Video",
    "File",
    "Cache",
)


def _collect(
    app_id: str,
    app_name: str,
    roots: list[str],
    subdirs: tuple[str, ...],
    out: list[SocialRule],
) -> None:
    """在每款软件的每个账号目录下，把白名单子目录登记成一条规则。"""
    for root in roots:
        if not os.path.isdir(root):
            continue
        accounts = _children(root)
        if not accounts:
            continue
        for account in accounts:
            account_name = os.path.basename(account)
            for sub in subdirs:
                full = os.path.join(account, sub)
                if not os.path.isdir(full):
                    continue
                out.append(
                    SocialRule(
                        id=f"social_{app_id}_{account_name}_{sub}".replace("\\", "_"),
                        name=f"{app_name} · {sub.split(chr(92))[-1]}",
                        app=app_name,
                        paths=[full],
                    )
                )


def discover() -> list[SocialRule]:
    """扫描本机装了哪些社交软件，返回可清理的缓存目录。"""
    docs = winpath.documents()
    appdata = os.environ.get("APPDATA", "")
    local = os.environ.get("LOCALAPPDATA", "")
    rules: list[SocialRule] = []

    # 微信 3.x / 4.x（4.0 改了目录名）
    _collect(
        "wechat",
        "微信",
        [os.path.join(docs, "WeChat Files"), os.path.join(docs, "xwechat_files")],
        _WECHAT_SUBDIRS,
        rules,
    )

    # QQ
    _collect(
        "qq",
        "QQ",
        [os.path.join(docs, "Tencent Files")],
        _QQ_SUBDIRS,
        rules,
    )

    # 企业微信
    _collect(
        "wxwork",
        "企业微信",
        [os.path.join(docs, "WXWork")],
        _WXWORK_SUBDIRS,
        rules,
    )

    # 钉钉：账号目录在 AppData 下
    if appdata:
        _collect(
            "dingtalk",
            "钉钉",
            [os.path.join(appdata, "DingTalk")],
            _DINGTALK_SUBDIRS,
            rules,
        )
    if local:
        _collect(
            "dingtalk",
            "钉钉",
            [os.path.join(local, "DingTalk")],
            _DINGTALK_SUBDIRS,
            rules,
        )

    return rules
