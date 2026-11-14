"""Windows系统管家 —— 启动入口。

用法：
    python main.py              # 正常启动
    python main.py --minimized  # 开机自启时静默启动到托盘
    python main.py --elevated   # 提权重启（内部使用，会延长单实例等待）
    python main.py --check-update  # 只跑一次检查更新并打印结果（排查用）
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from core import config, single_instance
from ui import theme_dark
from ui.main_window import MainWindow


def _check_update_cli() -> int:
    """命令行跑一次检查更新。打包后用它验证联网是否正常。"""
    import platform

    from core import updater, version

    print(f"版本：{version.text()}")
    print(f"平台：{platform.platform()}")
    info = updater.check()
    lines = [
        f"版本：{version.text()}",
        f"平台：{platform.platform()}",
        f"ok={info.ok} 最新={info.latest} 有更新={info.has_update}",
    ]
    if info.error:
        lines.append(f"错误：{info.error}")
    for line in lines:
        print(line)
    # 打包后是 --windowed，没有控制台，结果同时写一份文件便于排查
    try:
        import os
        import tempfile

        path = os.path.join(tempfile.gettempdir(), "wm_update_check.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
    except OSError:
        pass
    return 0 if info.ok else 1


def main() -> int:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "MemoryKeeper.Desktop.1"
        )
    except Exception:
        pass

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Windows系统管家")
    app.setQuitOnLastWindowClosed(False)

    settings = config.Settings.load()
    settings.autostart = config.is_autostart_enabled()

    # 样式表必须在建窗口之前套上，否则首帧会闪一下默认配色
    theme_dark.set_accent(settings.accent or None)
    theme_dark.set_theme(settings.theme)
    app.setStyleSheet(theme_dark.app_stylesheet())

    window = MainWindow(settings)

    start_hidden = "--minimized" in sys.argv or settings.start_minimized
    if not start_hidden:
        window.show()
    else:
        window.hide()

    return app.exec()


if __name__ == "__main__":
    # 排查用的命令行模式：不抢单实例锁，否则会被已运行的实例直接吞掉
    if "--check-update" in sys.argv:
        sys.exit(_check_update_cli())

    # 提权重启时旧进程可能还在收尾，多等几秒再放弃
    wait = 6.0 if "--elevated" in sys.argv else 0.8
    if not single_instance.acquire(wait):
        # 已有实例：把它的窗口叫到前台，而不是让用户以为程序没反应
        single_instance.activate_existing()
        sys.exit(0)
    try:
        code = main()
    finally:
        # 无论正常退出还是异常，都要把锁交出来
        single_instance.release()
    sys.exit(code)
