"""应用日志。

在此之前整个项目**没有任何日志**：`except Exception: pass` 有 25 处，
未捕获异常也没人接。用户说「内存数字不对」「页面是空的」，除了让他复现，
手上一条线索都没有。

这里只做三件事：

1. 把日志写到 `%APPDATA%\\Windows系统管家\\logs\\app.log`，滚动保留 5 份
2. 接管 `sys.excepthook`，未捕获的异常至少留下一份 traceback
3. 打包后 `--windowed` 没有控制台，`sys.stderr` 是 None —— PySide6 槽函数里
   抛的异常原本直接消失，这里把 stderr 也接进日志

不做远程上报：这个软件的卖点是「纯本地、不联网」，日志只留在用户机器上，
要不要发出来由用户自己决定。
"""

from __future__ import annotations

import io
import logging
import os
import platform
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

_MAX_BYTES = 1_000_000
_BACKUPS = 5
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATE = "%Y-%m-%d %H:%M:%S"

_configured = False
_log_path: Path | None = None
_THROTTLE = 300.0
_last_seen: dict[str, float] = {}


def log_dir() -> Path:
    """日志目录。

    `config` 是延迟导入的：配置模块自己也要记日志（它文件损坏时最需要），
    模块级互相 import 会成环。函数里 import 没有这个问题。
    """
    from . import config

    return config.config_dir() / "logs"


def log_path() -> Path:
    """当前日志文件路径（未 setup 时也返回预期路径）。"""
    return _log_path or (log_dir() / "app.log")


def setup(version_text: str = "") -> Path:
    """配置根 logger，幂等。返回日志文件路径。"""
    global _configured, _log_path
    if _configured:
        return log_path()

    directory = log_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        # 目录建不出来就退化成不落盘，但不要把程序拖死
        _configured = True
        _log_path = Path(os.devnull)
        return _log_path

    _log_path = directory / "app.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    handler = RotatingFileHandler(
        _log_path, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(_FORMAT, _DATE))
    handler.setLevel(logging.DEBUG)
    root.addHandler(handler)

    # 开发运行时同时打到控制台；打包后 stderr 为 None，跳过
    if sys.stderr is not None and sys.stderr.isatty():
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter(_FORMAT, _DATE))
        console.setLevel(logging.INFO)
        root.addHandler(console)

    _configured = True
    _write_banner(version_text)
    return _log_path


def _write_banner(version_text: str) -> None:
    """每个进程写一段环境信息，排查时先看这段。"""
    log = logging.getLogger("app")
    log.info("─" * 60)
    log.info(
        "启动 %s · Python %s · %s",
        version_text or "?",
        platform.python_version(),
        "管理员" if _is_admin() else "普通用户",
    )
    log.info("系统 %s · %s", platform.platform(), platform.machine())
    log.info("日志 %s", log_path())


def _is_admin() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def silenced(where: str, level: int = logging.DEBUG) -> None:
    """「故意吞掉一个异常」的留痕入口。

    项目里有大量「取不到就算了，用默认值继续」的地方 —— 这个降级本身是对的，
    错的是连一行字都不留：用户看到的是空列表或「—」，却不知道是没数据还是
    读取失败。

    `where` 写「模块.函数」，日志里能直接定位。逐项采集用 DEBUG（一条传感器
    读不到不该刷屏），整块功能失败用 `failed()`。

    **同一个位置 5 分钟内只记一次**：指标采集每秒跑一轮，某个传感器持续失败
    会在一小时内写满 1 MB 日志，把真正有用的信息挤掉。
    """
    now = time.monotonic()
    if now - _last_seen.get(where, 0.0) < _THROTTLE:
        return
    _last_seen[where] = now
    logging.getLogger("silenced").log(level, "%s 未取到，已降级", where, exc_info=True)


def failed(where: str) -> None:
    """整块功能取不到数据 —— 用户会看到空列表或全「—」，值得一条 WARNING。"""
    silenced(where, logging.WARNING)


def install_excepthook(on_crash=None) -> None:
    """接管未捕获异常。

    `on_crash(摘要)` 会在主线程之外被调用，实现方自己负责切回主线程 ——
    异常发生的那一刻界面状态是未知的，弹窗要推迟到事件循环下一轮。
    """
    log = logging.getLogger("crash")

    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log.error("未捕获异常", exc_info=(exc_type, exc_value, exc_tb))
        if on_crash is not None:
            try:
                on_crash(f"{exc_type.__name__}: {exc_value}")
            except Exception:
                log.exception("崩溃提示本身又失败了")

    sys.excepthook = _hook

    def _thread_hook(args):
        if issubclass(args.exc_type, SystemExit):
            return
        log.error(
            "线程 %s 未捕获异常",
            getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = _thread_hook


class _LogStream(io.TextIOBase):
    """把 stderr 接进日志。

    打包成 `--windowed` 后 `sys.stderr` 是 None，PySide6 在槽函数里捕获到的
    异常会直接丢掉 —— 这类异常不会走 `sys.excepthook`，只能从 stderr 捞。
    """

    _LEVELS = {"ERROR": logging.ERROR, "WARNING": logging.WARNING}

    def __init__(self) -> None:
        self._buffer = ""

    def write(self, text: str) -> int:
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.rstrip()
            if not line:
                continue
            level = logging.WARNING if "Traceback" in line else logging.INFO
            for key, value in self._LEVELS.items():
                if line.startswith(key):
                    level = value
            logging.getLogger("stderr").log(level, "%s", line)
        return len(text)

    def flush(self) -> None:
        return None


def capture_stderr() -> None:
    """stderr / stdout 不可用（打包后 --windowed）时改接到日志。

    两个都接：`--check-update` 这类命令行模式本来是 print 结果的，
    stdout 为 None 时连 print 都会抛 AttributeError。
    """
    if sys.stderr is None:
        sys.stderr = _LogStream()
    if sys.stdout is None:
        sys.stdout = _LogStream()


def recent(lines: int = 200) -> str:
    """取日志末尾若干行，给「导出诊断信息」用。"""
    path = log_path()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            content = handle.readlines()
    except OSError:
        return ""
    return "".join(content[-lines:])


def diagnostics(version_text: str = "") -> str:
    """一段可直接粘给开发者的环境摘要 + 日志尾部。"""
    head = [
        f"版本：{version_text}",
        f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"系统：{platform.platform()}",
        f"Python：{platform.python_version()}",
        f"管理员：{'是' if _is_admin() else '否'}",
        f"日志文件：{log_path()}",
        "─" * 60,
    ]
    return "\n".join(head) + "\n" + recent(400)
