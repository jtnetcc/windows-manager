"""Windows 激活状态与激活时间。

三处数据源，都不需要管理员：

- **`slmgr.vbs /dlv`**（经 cscript）拿许可证状态、产品密钥通道、部分产品密钥。
  一次约 1.7 秒 —— cscript 起进程本身就要一秒多，所以调用方必须放到后台线程，
  这里另外做 10 分钟缓存，换肤重建界面不会重复起进程。
- **`spp\\store\\2.0\\tokens.dat` 的创建时间**当「系统激活时间」。激活落地时
  SPP 会建这个许可证库；之后每次联网校验只改写内容（mtime 变），创建时间不动，
  所以重新激活后的日常校验不会把日期刷成今天。
- **`SYSTEM\\Setup\\CloneTag`** 当「硬件激活时间」。OEM 机器出厂前 sysprep
  封装镜像会写下这个时间戳，主板固化的数字许可证就是那一刻绑定的。注册表里
  没有时退回 `ClipSVC\\Install\\Apps` 目录的创建时间（同为出厂映像写入时刻）。

「激活时间」在系统里没有权威字段可读：微软不落盘，许可证只在服务器侧与硬件
哈希绑定。所以这两项都是**痕迹推定**，界面上的 tooltip 要如实说明来源。
"""

from __future__ import annotations

import calendar
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass

from .sysinfo import reg

CREATE_NO_WINDOW = 0x08000000

_SYSTEM_ROOT = os.environ.get("SystemRoot") or r"C:\Windows"
_CSCRIPT = os.path.join(_SYSTEM_ROOT, "System32", "cscript.exe")
_SLMGR = os.path.join(_SYSTEM_ROOT, "System32", "slmgr.vbs")
_TOKENS = os.path.join(_SYSTEM_ROOT, "System32", "spp", "store", "2.0", "tokens.dat")
_CLIP_INSTALL = r"C:\ProgramData\Microsoft\Windows\ClipSVC\Install\Apps"

CACHE_SECONDS = 600.0

# slmgr 的字段名会随系统语言变，中英两套都认。认不出来时整行跳过，
# 不影响其它字段 —— 部分产品的输出里连「名称」都没有。
_FIELDS = {
    "status": ("许可证状态", "license status"),
    "channel": ("产品密钥通道", "product key channel"),
    "partial": ("部分产品密钥", "partial product key"),
    "name": ("名称", "name"),
}

# 通道 → 人话。没列到的原样显示，宁可露出 "Volume:CSVLK" 也不猜。
_CHANNELS = {
    "OEM:DM": "OEM 数字许可证（主板固化）",
    "OEM:SLP": "OEM 预装密钥",
    "OEM:NONSLP": "OEM 密钥（未预装）",
    "RETAIL": "零售版数字许可证",
    "VOLUME:MAK": "批量授权（MAK）",
    "VOLUME:GVLK": "批量授权（KMS 客户端）",
    "VOLUME:CSVLK": "批量授权（KMS 主机）",
}

# 状态原文也是本地化的，只认最常见的几个；其余原样显示，不硬译。
_STATUS = {
    "已授权": "已激活",
    "licensed": "已激活",
    "通知": "未激活",
    "notification": "未激活",
    "宽限期": "宽限期",
    "grace": "宽限期",
    "oob grace": "待激活",
}


@dataclass
class ActivationInfo:
    status: str = ""          # 已激活 / 未激活 / 宽限期
    method: str = ""          # OEM 数字许可证（主板固化）
    channel: str = ""         # OEM:DM，原始通道名
    product_key: str = ""     # 完整密钥，拿不到时为部分密钥
    partial_key: str = ""     # XQBP2
    product_id: str = ""      # 00330-52296-51722-AAOEM
    system_time: str = ""     # 系统激活时间，YYYY-MM-DD HH:MM
    system_note: str = ""     # 上面这一项是哪来的
    hardware_time: str = ""   # 硬件激活时间，YYYY-MM-DD
    hardware_note: str = ""
    queried: bool = False     # slmgr 是否真的跑出了结果

    @property
    def method_note(self) -> str:
        """给「激活方式」当 tooltip：通道原始名 + 密钥来源。"""
        bits = [self.channel] if self.channel else []
        if self.partial_key:
            bits.append(f"密钥末五位 {self.partial_key}")
        return " · ".join(bits)

    @property
    def display_key(self) -> str:
        """整key读不到时退成末五位 —— 只显示 XQBP2 也比空着有用。"""
        return self.product_key or self.partial_key


_CACHE: tuple[float, ActivationInfo] | None = None
_LOCK = threading.Lock()


# ---------------------------------------------------------------- slmgr


def _decode(raw: bytes) -> str:
    """cscript 按控制台代码页输出，中文机器上是 GBK。

    不能用 `//U` 强转 UTF-16：那个开关只在有控制台时有效，加了
    CREATE_NO_WINDOW 之后输出直接是 0 字节。
    """
    for encoding in ("utf-8", "mbcs", "gbk"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1", "replace")


def _run_slmgr() -> str:
    if not os.path.isfile(_SLMGR):
        return ""
    try:
        proc = subprocess.run(
            [_CSCRIPT, "//nologo", _SLMGR, "/dlv"],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=25,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    # 未激活时 slmgr 也返回 0，只是内容不同，所以不看返回码
    return _decode(proc.stdout)


def _parse(text: str) -> dict[str, str]:
    """按「标签: 值」切行。标签是中英双语白名单，值里可能还有冒号
    （`产品密钥通道: OEM:DM`），所以只按**第一个**冒号切。"""
    found: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line and "：" not in line:
            continue
        name, _, value = line.replace("：", ":").partition(":")
        name = name.strip().lower()
        value = value.strip()
        for field, labels in _FIELDS.items():
            if field not in found and name in labels:
                found[field] = value
    return found


def _guess_channel(text: str) -> str:
    """兜底：字段名没认出来（第三国语言）时，从全文里捞通道名。

    只在标签解析失败后才用 —— 描述行里也带 `OEM_DM channel`，
    正常情况不该让它决定结果。
    """
    match = re.search(
        r"\b(OEM|Retail|Volume)[:_ ]?(DM|SLP|NONSLP|MAK|GVLK|CSVLK)?\b",
        text,
        re.IGNORECASE,
    )
    if not match:
        return ""
    head = match.group(1).upper()
    tail = (match.group(2) or "").upper()
    return f"{head}:{tail}" if tail else head


def _normalize(channel: str) -> str:
    channel = channel.strip().replace("_", ":").upper()
    return channel if channel != "RETAIL:" else "RETAIL"


def _fill_license(info: ActivationInfo) -> None:
    text = _run_slmgr()
    if not text:
        return
    data = _parse(text)
    info.queried = True

    raw_status = data.get("status", "")
    info.status = _STATUS.get(raw_status.lower(), raw_status)

    info.channel = _normalize(data.get("channel") or _guess_channel(text))
    info.method = _CHANNELS.get(info.channel, info.channel)

    info.partial_key = data.get("partial", "")
    key = reg(
        r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\SoftwareProtectionPlatform",
        "BackupProductKeyDefault",
    )
    info.product_key = key if isinstance(key, str) and "-" in key else ""

    info.product_id = reg(
        r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "ProductId"
    ) or ""


# ---------------------------------------------------------------- 时间


def _fill_times(info: ActivationInfo) -> None:
    info.system_time, info.system_note = _system_time()
    info.hardware_time, info.hardware_note = _hardware_time()


def _system_time() -> tuple[str, str]:
    try:
        stat = os.stat(_TOKENS)
    except OSError:
        return "", "读不到许可证库文件，无法推定"
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_ctime))
    return stamp, "取许可证库 tokens.dat 的创建时间，OEM 预装机通常落在安装当天"


def _hardware_time() -> tuple[str, str]:
    tag = reg(r"SYSTEM\Setup", "CloneTag")
    if isinstance(tag, list):
        tag = tag[0] if tag else ""
    if isinstance(tag, str) and tag:
        try:
            # 形如 'Sat Dec 07 09:18:25 2019'，UTC
            parsed = time.strptime(tag, "%a %b %d %H:%M:%S %Y")
            local = time.localtime(calendar.timegm(parsed))
            return (
                time.strftime("%Y-%m-%d", local),
                "OEM 出厂封装（sysprep）时间，主板固化的许可证即此时绑定",
            )
        except ValueError:
            pass

    # sysprep 记录被清掉时，出厂映像写入 ClipSVC 的时刻还在
    try:
        stat = os.stat(_CLIP_INSTALL)
    except OSError:
        return "", "本机没有 OEM 出厂预装记录（自装机或重装过）"
    return (
        time.strftime("%Y-%m-%d", time.localtime(stat.st_ctime)),
        "OEM 出厂映像写入时间（ClipSVC 安装目录创建时间）",
    )


# ---------------------------------------------------------------- 入口


def collect(use_cache: bool = True) -> ActivationInfo:
    """采集激活信息。四步互不牵连，某一步失败只是那几格空着。"""
    global _CACHE

    if use_cache:
        with _LOCK:
            if _CACHE is not None and time.time() - _CACHE[0] < CACHE_SECONDS:
                return _CACHE[1]

    info = ActivationInfo()
    for step in (_fill_license, _fill_times):
        try:
            step(info)
        except Exception:
            continue

    with _LOCK:
        _CACHE = (time.time(), info)
    return info


def clear_cache() -> None:
    global _CACHE
    with _LOCK:
        _CACHE = None
