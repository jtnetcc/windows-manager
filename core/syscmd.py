"""统一的子进程封装。

为什么要单独一个模块：`slim` / `startup` / `diskinfo` / `activation` / `health` /
`metrics` 各自写了一遍 `subprocess.run(...)`，每份都带 `CREATE_NO_WINDOW`、
各自的编码猜测和超时处理。少写一处 `creationflags`，界面上就会闪一个黑窗口；
少写一处 timeout，某个系统命令卡住就会把任务线程一起拖死。

三条约定（和 `docs/交接文档.md` 6.48 一致）：
1. **一律带 `CREATE_NO_WINDOW`** —— 本程序是 GUI 进程，起控制台程序会弹窗；
2. **一律带超时**，默认 30 秒，DISM 这类慢命令自己传；
3. **解码失败不抛异常**，用 `errors="replace"`，调用方拿到的永远是字符串。

返回值风格和 `slim._run` 保持历史兼容：`(退出码, 文本)`，超时返回 `(-1, "命令超时")`。
"""

from __future__ import annotations

import subprocess

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Windows 的中文系统命令（sc/schtasks/fsutil）输出的是 ANSI 代码页
DEFAULT_ENCODING = "mbcs"


def run(
    args: list[str] | tuple[str, ...],
    timeout: float = 30,
    encoding: str = DEFAULT_ENCODING,
) -> tuple[int, str]:
    """跑一条命令，返回 (退出码, stdout+stderr 合并文本)。

    超时返回 (-1, "命令超时")，启动失败返回 (-1, 异常描述) —— 调用方不用
    区分异常类型，看 code 是否为负即可。
    """
    try:
        done = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            encoding=encoding,
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        text = (done.stdout or "") + (done.stderr or "")
        return done.returncode, text.strip()
    except subprocess.TimeoutExpired:
        return -1, "命令超时"
    except OSError as exc:
        return -1, str(exc)


def run_bytes(
    args: list[str] | tuple[str, ...], timeout: float = 30
) -> tuple[int, bytes]:
    """同上，但返回原始字节。

    给「输出编码不确定」的场景用：`slmgr` / `powershell` / `fsutil` 在中文
    系统上可能是 GBK、UTF-8 或 UTF-16，调用方自己按顺序试。
    """
    try:
        done = subprocess.run(
            list(args),
            capture_output=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        return done.returncode, (done.stdout or b"") + (done.stderr or b"")
    except subprocess.TimeoutExpired:
        return -1, b""
    except OSError:
        return -1, b""


def capture(
    args: list[str] | tuple[str, ...],
    timeout: float = 30,
    encoding: str = DEFAULT_ENCODING,
) -> str:
    """只要文本，失败给空串（适合「拿不到就当没有」的探测型调用）。"""
    code, text = run(args, timeout=timeout, encoding=encoding)
    return text if code >= 0 else ""


def ok(args: list[str] | tuple[str, ...], timeout: float = 30) -> bool:
    """只关心成功与否。"""
    code, _text = run(args, timeout=timeout)
    return code == 0
