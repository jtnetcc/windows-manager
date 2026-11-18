"""验证「卸载不弹黑窗口」。

两条路径都要验，因为它们是两个不同的弹窗来源：

1. **软件管理页点「卸载」** —— 以前用 `shell=True`，等于先起一个 cmd.exe，
   而本进程没有控制台，Windows 给 cmd 新开一个控制台窗口。这里拦截
   `subprocess.Popen`，确认调用参数里既没有 `cmd` 也没有 `shell=True`。
2. **卸载本程序** —— 卸载器退出前要删自己所在的目录，得托一个 cmd 干。
   这里在真实卸载过程中轮询屏幕上的控制台窗口（`ConsoleWindowClass`），
   确认没有新窗口出现，并且安装目录最终被删掉。

用法：
    python tools/check_uninstall_silent.py            # 两条都跑
    python tools/check_uninstall_silent.py --no-install   # 只跑第 1 条

第 2 条需要 dist/ 里有 setup.exe；没有会跳过并提示。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import apps  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def console_windows() -> set[tuple[int, str]]:
    """当前屏幕上所有**可见**的控制台窗口：(进程号, 标题)。"""
    found: set[tuple[int, str]] = set()

    def callback(hwnd, _param):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value != "ConsoleWindowClass":
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        length = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        found.add((pid.value, title.value))
        return True

    user32.EnumWindows(EnumProc(callback), 0)
    return found


def check_launch_uninstaller() -> None:
    """第 1 条：点「卸载」不该经过 cmd。"""
    captured: dict = {}
    real_popen = subprocess.Popen

    class Stub:
        def __init__(self, args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

    apps.subprocess.Popen = Stub          # type: ignore[attr-defined]
    try:
        ok, error = apps.launch_uninstaller(
            apps.InstalledApp(
                name="测试程序",
                uninstall=r'"C:\Windows\System32\notepad.exe" %ProgramFiles%',
            )
        )
    finally:
        apps.subprocess.Popen = real_popen  # type: ignore[attr-defined]

    assert ok, f"启动失败：{error}"
    args = str(captured["args"])
    kwargs = captured["kwargs"]
    assert kwargs.get("shell") is not True, f"还在用 shell=True：{kwargs}"
    assert "cmd" not in args.lower(), f"命令行里出现了 cmd：{args}"
    assert "%ProgramFiles%" not in args, "环境变量没展开，CreateProcess 不认 %VAR%"
    print(f"点「卸载」不走 cmd ✓（命令行：{args[:60]}…）")


def check_uninstaller_window(setup: Path) -> None:
    """第 2 条：卸载器托 cmd 删目录时不留可见窗口。"""
    target = Path(tempfile.gettempdir()) / "wm_silent_test"
    if target.exists():
        import shutil

        shutil.rmtree(target, ignore_errors=True)

    print(f"  静默安装到 {target}")
    subprocess.run(
        [str(setup), "/S", "/D=" + str(target)], check=False, timeout=180
    )
    assert (target / "uninstall.exe").exists(), "安装没成功，后面的验证没意义"

    before = console_windows()
    started = time.time()
    process = subprocess.Popen(
        [str(target / "uninstall.exe"), "/S"], close_fds=True
    )

    seen: set[tuple[int, str]] = set()
    while process.poll() is None or time.time() - started < 3:
        seen |= console_windows() - before
        if time.time() - started > 20:
            break
        time.sleep(0.05)

    # 等目录被批处理删掉
    gone_at = None
    deadline = time.time() + 25
    while time.time() < deadline:
        if not target.exists():
            gone_at = time.time() - started
            break
        # 卸载器退出之后 cmd 才动手，这期间继续盯着窗口
        seen |= console_windows() - before
        time.sleep(0.1)

    if seen:
        detail = ", ".join(f"PID {pid}「{title}」" for pid, title in sorted(seen))
        raise AssertionError(f"卸载过程中弹出了控制台窗口：{detail}")
    if gone_at is None:
        raise AssertionError("安装目录没被删掉（卸载失败）")

    print(f"卸载全程无控制台窗口 ✓（目录在 {gone_at:.1f}s 后消失）")


def main() -> int:
    check_launch_uninstaller()

    if "--no-install" in sys.argv:
        return 0

    setups = sorted((ROOT / "dist").glob("*setup.exe"))
    if not setups:
        print("跳过第 2 条：dist/ 里没有 setup.exe（先跑 tools/build_installer.py）")
        return 0

    check_uninstaller_window(setups[-1])
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
