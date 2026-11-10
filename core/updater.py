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


def _fetch(url: str, use_proxy: bool) -> dict:
    request = urllib.request.Request(url)
    request.add_header("User-Agent", f"WindowsManager/{version.VERSION}")
    # 显式控制代理：OSS 在 cn-beijing，直连最快；
    # 但有些环境（公司网关、代理软件）必须走代理，所以两种都试一遍。
    if use_proxy:
        opener = urllib.request.build_opener()
    else:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def check() -> UpdateInfo:
    """拉取最新版本信息。任何异常都转成 ok=False，不抛给界面。

    先直连、再走系统代理。Windows 上 urllib 默认会读注册表里的系统代理，
    用户开着代理软件时直连请求会被塞进代理，而代理未必放行这个域名 ——
    这就是「开发时能检查、打包后检查失败」最常见的原因。
    """
    url = f"https://{_HOST}/{_MANIFEST_KEY}"
    data = None
    errors = []
    for use_proxy in (False, True):
        try:
            data = _fetch(url, use_proxy)
            break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            errors.append(f"{'代理' if use_proxy else '直连'}：{exc}")

    if data is None:
        return UpdateInfo(ok=False, error="；".join(errors))

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
