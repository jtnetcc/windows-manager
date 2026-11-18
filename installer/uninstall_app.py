"""卸载程序（Windows）。

和安装器一起放两个 exe 里的那一个，**不依赖 Qt**：卸载器只需要几个对话框，
引 PySide6 会让它从 8 MB 涨到 30 MB，而它一辈子只会被点一次。

它做五件事：删快捷方式 → 删注册表里的卸载登记 → 问要不要删用户数据 →
删安装目录（用 detach 的 cmd 延后删除，因为自己就在这个目录里）→ 退出。

参数：/S 静默卸载（保留用户数据），/S /data 连数据一起删。
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import winreg
from pathlib import Path

APP_ID = "Windows系统管家"
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}"
MB_YESNO = 0x04
MB_ICONQUESTION = 0x20
MB_ICONINFORMATION = 0x40
IDYES = 6


def msg(text: str, title: str = APP_ID, flags: int = MB_ICONINFORMATION) -> int:
    return ctypes.windll.user32.MessageBoxW(0, text, title, flags)


def remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def cleanup_links() -> None:
    appdata = os.environ.get("APPDATA", "")
    profile = os.environ.get("USERPROFILE", "")
    if appdata:
        menu = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        remove(menu / f"{APP_ID}.lnk")
        remove(menu / f"卸载 {APP_ID}.lnk")
    if profile:
        remove(Path(profile) / "Desktop" / f"{APP_ID}.lnk")


def cleanup_registry() -> None:
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass


def data_dir() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "MemoryKeeper"


def schedule_removal(folder: Path) -> None:
    """延后删除自己：先落一个批处理，再由它等两秒后删目录。

    为什么不用一条 `cmd /c "..."` 直接删：`for /l` 的语法在命令行和批处理里
    不一样（`%i` vs `%%i`），还夹着 `&`、括号、引号，转义很容易出岔子 ——
    实测就是这个原因导致 rmdir 一次都没执行，目录里只剩一个 uninstall.exe。
    写成 .bat 文件后语法就是批处理的，出了问题还能打开文件看。

    批处理做三件事：等两秒等本进程退出 → 最多重试 20 次删目录 → 删掉自己。
    重试之间隔一秒：杀软扫描、资源管理器缩略图都可能短暂占着文件。

    **窗口为什么不能弹**：`cmd.exe` 是控制台程序，我们又是 GUI 进程、没有
    控制台，如果只给 `DETACHED_PROCESS`，cmd 会自己新开一个控制台 ——
    屏幕上就是黑窗口一闪。而且 `DETACHED_PROCESS` 和 `CREATE_NO_WINDOW`
    本身是互斥的两个意图，同时给的行为没有保证。正确做法只有一条：
    给 `CREATE_NO_WINDOW`（分配一个**不显示**的控制台），
    再把三个标准流都接到空设备 —— 连重定向都不留给它。
    """
    neutral = Path(os.environ.get("TEMP") or str(Path.home()))
    script = neutral / "wm-uninstall.bat"
    body = (
        "@echo off\r\n"
        # 用 ping 而不是 timeout 做延时：timeout 需要可用的控制台输入，
        # 标准输入被重定向时它会直接报错退出，延时也就没了
        "ping -n 3 127.0.0.1 >nul 2>&1\r\n"
        "for /l %%i in (1,1,20) do (\r\n"
        f'  rmdir /s /q "{folder}" >nul 2>&1\r\n'
        f'  if not exist "{folder}" goto done\r\n'
        "  ping -n 2 127.0.0.1 >nul 2>&1\r\n"
        ")\r\n"
        ":done\r\n"
        'del "%~f0" >nul 2>&1\r\n'
    )
    try:
        script.write_text(body, encoding="mbcs", errors="replace")
    except OSError:
        shutil.rmtree(folder, ignore_errors=True)
        return

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(
            ["cmd", "/c", str(script)],
            cwd=str(neutral),          # cwd 在安装目录里的话，rmdir 必然失败
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
    except OSError:
        shutil.rmtree(folder, ignore_errors=True)


def main() -> int:
    argv = sys.argv[1:]
    silent = "/S" in argv or "--silent" in argv
    drop_data = "/data" in argv or "--data" in argv

    folder = Path(sys.executable).parent

    if not silent:
        if msg(
            f"确定要卸载 {APP_ID} 吗？\n\n安装位置：{folder}",
            flags=MB_YESNO | MB_ICONQUESTION,
        ) != IDYES:
            return 0
        drop_data = msg(
            "要一并删除配置与统计吗？\n\n"
            f"{data_dir()}\n\n"
            "选「否」会保留（重装后历史数据还在）。",
            flags=MB_YESNO | MB_ICONQUESTION,
        ) == IDYES

    cleanup_links()
    cleanup_registry()

    if drop_data:
        shutil.rmtree(data_dir(), ignore_errors=True)

    # 程序本体的 exe 先删；卸不掉的留给 cmd 收尾
    for name in (f"{APP_ID}.exe", "README.txt", "manifest.json", "icon.png",
                 "uninstall.json"):
        remove(folder / name)

    schedule_removal(folder)

    if not silent:
        msg(f"{APP_ID} 已卸载。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
