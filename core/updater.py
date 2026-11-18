"""检查更新与下载入口。

联网只有两种场景，都是用户主动点的：「检查更新」和「关于 → 打开（下载）」。
没有后台轮询，其余功能全部纯本地。

**分发地址不写进界面**，也不以明文形式留在源码里：地址用异或混淆存放，
运行时才拼出来。要说清楚的是，这只是提高 `strings` 扫描的门槛，不是加密 ——
能拼出来的东西，逆向的人一定拼得出来。

所以真正管用的是后面这一层：**私有桶 + 短时效签名**。
桶设成私有之后，直链本身失效；客户端下载前先向自己的扫码函数换一个
十分钟有效的签名 URL。地址即使被截图、被转发，过了期限也拉不动文件。
见 `server/scf_sign/README.md`。
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import version

# ------------------------------------------------------------------ 地址
#
# 分发域名做了异或混淆，密钥和密文分开存放。
#
# 说明白：**这只是提高破解门槛，不是加密**。客户端里能拼出来的东西，
# 逆向的人一定能拼出来（密钥也在同一个 exe 里）。它挡掉的是
# `strings 软件.exe` 直接搜域名这类最省事的办法。
#
# 真正值得防的不是「地址被看见」，而是「更新包被替换」和「流量被别人刷」。
# 前者靠服务端：HTTPS + ACL + 发布流程控制；后者靠短时效签名，见下面 _ticket。
_KEY = bytes((0x9E, 0x3D, 0x71, 0xC4, 0x2B, 0x88, 0xF5, 0x16))
_ENC = bytes(
    (
        0xF4, 0x49, 0x1F, 0xA1, 0x5F, 0xEB, 0x96, 0x38,
        0xF1, 0x4E, 0x02, 0xE9, 0x48, 0xE6, 0xD8, 0x74,
        0xFB, 0x54, 0x1B, 0xAD, 0x45, 0xEF, 0xDB, 0x77,
        0xF2, 0x54, 0x08, 0xB1, 0x45, 0xEB, 0x86, 0x38,
        0xFD, 0x52, 0x1C,
    )
)


def _xor(key: bytes, data: bytes) -> str:
    """把混淆过的字节还原成字符串（同一函数加解密方向相同）。"""
    return bytes(byte ^ key[i % len(key)] for i, byte in enumerate(data)).decode()


_HOST = _xor(_KEY, _ENC)
_MANIFEST_KEY = "windows-manager/latest/version.json"
_EXE_KEY = "windows-manager/latest/Windows-Manager.exe"

# ------------------------------------------------------------------ 换签名
#
# 扫码函数（云函数 + API 网关）的地址。**留空 = 还没部署**，此时全部退回
# 旧的直链方式，程序照常能用。部署完之后跑一次
#
#     python tools/obfuscate.py https://你的函数地址/
#
# 把输出的两个 bytes() 贴到下面，重新打包即可。
_SIGN_KEY = bytes((0x3C, 0xA7, 0x41, 0xD9, 0x62, 0x1E, 0x85, 0xFB))
_SIGN_ENC = bytes(())          # ← 部署好之后填这里

_SIGN_BASE = _xor(_SIGN_KEY, _SIGN_ENC) if _SIGN_ENC else ""

# 服务端签多久由函数的环境变量决定，这里只是签名里没带 expires 时的默认值。
SIGN_TTL = 600

# 提前这么多秒就重取，别等到最后一刻过期。
_REFRESH_AHEAD = 60

TIMEOUT = 3

# 连接层缓存：SSL 上下文、两种 opener、系统代理判断。
# 全都只建一次 —— 每次请求重建这些，在打包后的 exe 里是秒级开销。
_SSL_CTX = None
_OPENERS: dict[bool, urllib.request.OpenerDirector] = {}
_PROXY_KNOWN: bool | None = None
_PROXY_AT = 0.0


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
        return self.ok and bool(self.latest) and _tuple(self.latest) > _tuple(version.VERSION)


@dataclass
class _Ticket:
    """一次换签的结果。签名会过期，所以缓存要带上到期时间。"""

    url: str
    expires_at: float      # time.monotonic()，不是墙上时钟 —— 改系统时间不影响它


# 上一次检查结果。页面会因为换肤被重建，重建后要能把结果接回去。
_LAST: UpdateInfo | None = None

_ticket: _Ticket | None = None


def _tuple(text: str) -> tuple[int, ...]:
    """把 0.0.16 拆成可比较的元组，非法版本号按 0 处理。"""
    parts = []
    for chunk in str(text).split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _ssl_context() -> ssl.SSLContext:
    """HTTPS 上下文只建一次。

    这一条是打包后启动慢的真凶之一：`build_opener()` 每调一次都会新造一个
    HTTPS 处理器，进而枚举一遍 Windows 证书存储。源码模式下这一步是毫秒级，
    冻结成 exe 之后实测 5 秒起步 —— 直连 + 代理各一次，11 秒就没了。
    复用同一个 context 之后这笔开销只付一次。
    """
    global _SSL_CTX
    if _SSL_CTX is None:
        _SSL_CTX = ssl.create_default_context()
    return _SSL_CTX


def _opener(use_proxy: bool) -> urllib.request.OpenerDirector:
    """按「走不走系统代理」缓存两个 opener，别每次请求都重建。"""
    opener = _OPENERS.get(use_proxy)
    if opener is None:
        handlers: list[urllib.request.BaseHandler] = [
            urllib.request.HTTPSHandler(context=_ssl_context())
        ]
        if not use_proxy:
            handlers.append(urllib.request.ProxyHandler({}))
        opener = urllib.request.build_opener(*handlers)
        _OPENERS[use_proxy] = opener
    return opener


def _proxy_configured() -> bool:
    """系统里配了代理没有。配了就优先走代理 —— 用户既然开了代理软件，
    直连多半是死路，先试直连等于白等一轮超时。"""
    global _PROXY_KNOWN, _PROXY_AT
    now = time.monotonic()
    if _PROXY_KNOWN is None or now - _PROXY_AT > 60:
        try:
            proxies = urllib.request.getproxies()
            _PROXY_KNOWN = bool(proxies.get("https") or proxies.get("http"))
        except Exception:
            _PROXY_KNOWN = False
        _PROXY_AT = now
    return _PROXY_KNOWN


def _fetch(url: str, use_proxy: bool, timeout: float = TIMEOUT) -> dict:
    request = urllib.request.Request(url)
    request.add_header("User-Agent", f"WindowsManager/{version.VERSION}")
    with _opener(use_proxy).open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _get(url: str, timeout: float = TIMEOUT) -> tuple[dict | None, list[str]]:
    """按「先代理还是先直连」的顺序各试一次，返回 (数据, 错误列表)。

    顺序由系统代理决定，不再无脑直连优先：开着代理的机器上直连必然超时，
    那一轮纯属白等。
    """
    order = (True, False) if _proxy_configured() else (False, True)
    errors: list[str] = []
    deadline = time.monotonic() + timeout * len(order)
    for use_proxy in order:
        left = deadline - time.monotonic()
        if left <= 0.2:
            errors.append("剩余时间不足，跳过")
            break
        try:
            return _fetch(url, use_proxy, min(timeout, left)), errors
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            errors.append(f"{'代理' if use_proxy else '直连'}：{exc}")
    return None, errors


def _direct_url() -> str:
    return f"https://{_HOST}/{_EXE_KEY}"


def _sign_get(target: str) -> tuple[dict | None, list[str]]:
    """向扫码函数要一份数据。没配地址直接返回空，调用方自己走老路。"""
    if not _SIGN_BASE:
        return None, []
    return _get(f"{_SIGN_BASE.rstrip('/')}/{target}")


def _remember(data: dict) -> str:
    """把函数返回的签名 URL 记下来，返回它。"""
    global _ticket

    url = str(data.get("url", "")).strip()
    if not url:
        return ""
    try:
        ttl = float(data.get("expires") or SIGN_TTL)
    except (TypeError, ValueError):
        ttl = SIGN_TTL
    _ticket = _Ticket(url, time.monotonic() + ttl)
    return url


def download_url() -> str:
    """取一个当前可用的下载地址。

    **这个字符串只能拿去打开浏览器，别贴到任何 QLabel / tooltip / 剪贴板。**

    签名有效期内直接用缓存；过期了再换一次。换签失败就退回直链 ——
    桶是私有的话这一次会 403，但错误看得见，比「点了没反应」好排查。
    """
    if not _SIGN_BASE:
        return _direct_url()

    if _ticket and _ticket.expires_at - _REFRESH_AHEAD > time.monotonic():
        return _ticket.url

    data, _ = _sign_get("download")
    if data:
        url = _remember(data)
        if url:
            return url
    return _direct_url()


def check() -> UpdateInfo:
    """拉取最新版本信息。任何异常都转成 ok=False，不抛给界面。

    先直连、再走系统代理。Windows 上 urllib 默认会读注册表里的系统代理，
    用户开着代理软件时直连请求会被塞进代理，而代理未必放行这个域名 ——
    这就是「开发时能检查、打包后检查失败」最常见的原因。
    """
    global _LAST, _ticket

    # 配了扫码函数就先问它：桶私有之后清单本身也读不到，函数一次把
    # 版本号、体积和签名下载地址给全。
    data, errors = _sign_get("latest")
    if data is None:
        data, errors = _get(f"https://{_HOST}/{_MANIFEST_KEY}")

    if data is None:
        return UpdateInfo(ok=False, error="；".join(errors))

    latest = str(data.get("version", "")).strip()
    if not latest:
        return UpdateInfo(ok=False, error="版本信息缺少 version 字段")

    if data.get("url"):
        _remember(data)
    elif _ticket is None:
        # 老的清单里没有地址，本地拼一个；桶私有之后这条会失效，
        # 届时 check 走的已经是扫码函数，走不到这里。
        _ticket = _Ticket(_direct_url(), time.monotonic() + SIGN_TTL)

    _LAST = UpdateInfo(
        ok=True,
        latest=latest,
        notes=str(data.get("notes", "")).strip(),
        url=_ticket.url if _ticket else _direct_url(),
        size=str(data.get("size", "")).strip(),
    )
    return _LAST


def last_result() -> UpdateInfo | None:
    """上一次检查的结果。页面重建后用它恢复按钮状态。"""
    return _LAST
