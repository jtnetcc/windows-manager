"""检查更新。

唯一一处联网行为，而且**只在用户点「检查更新」时触发**，不做任何后台轮询。
其余功能全部纯本地。

分发地址不写进界面：界面上只有「检查更新」按钮和结果文案，
URL 本身留在本模块里（轻度编码，避免被字符串扫描直接抓到）。
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import version

# 分发地址。base64 只是避免明文出现在二进制里被随手 grep 到，
# 不是加密 —— 真要藏住只能靠服务端鉴权。
_HOST = base64.b64decode("anRuZXRjYy5vc3MtY24tYmVpamluZy5hbGl5dW5jcy5jb20=").decode()
_MANIFEST_KEY = "windows-manager/latest/version.json"
_EXE_KEY = "windows-manager/latest/Windows-Manager.exe"

TIMEOUT = 8


@dataclass
class UpdateInfo:
    ok: bool
    latest: str = ""
    notes: str = ""
    url: str = ""
    size: str = ""
    error: str = ""

    @property
    def has_update(self) -> bool:
        if not self.ok or not self.latest:
            return False
        return _tuple(self.latest) > _tuple(version.VERSION)


def _tuple(text: str) -> tuple[int, ...]:
    """把 0.0.16 拆成可比较的元组，非法版本号按 0 处理。"""
    parts = []
    for chunk in str(text).split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def check() -> UpdateInfo:
    """拉取最新版本信息。任何异常都转成 ok=False，不抛给界面。"""
    url = f"https://{_HOST}/{_MANIFEST_KEY}"
    request = urllib.request.Request(url)
    request.add_header("User-Agent", f"WindowsManager/{version.VERSION}")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return UpdateInfo(ok=False, error=f"网络不可用：{exc}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return UpdateInfo(ok=False, error="版本信息格式异常")

    latest = str(data.get("version", "")).strip()
    if not latest:
        return UpdateInfo(ok=False, error="版本信息缺少 version 字段")

    return UpdateInfo(
        ok=True,
        latest=latest,
        notes=str(data.get("notes", "")).strip(),
        # 下载地址不放在清单里，由本地拼出来，清单里不出现任何域名
        url=f"https://{_HOST}/{_EXE_KEY}",
        size=str(data.get("size", "")).strip(),
    )
