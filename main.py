"""Windows系统管家 —— 启动入口。

用法：
    python main.py              # 正常启动
    python main.py --minimized  # 开机自启时静默启动到托盘
    python main.py --elevated   # 提权重启（内部使用，会延长单实例等待）
    python main.py --check-update  # 只跑一次检查更新并打印结果（排查用）
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from core import applog, config, single_instance, version
from ui import theme_dark
from ui.main_window import MainWindow

# 一个进程只弹一次崩溃提示，避免循环报错时刷屏
_crash_reported = False


def _report_crash(window, message: str) -> None:
    """未捕获异常之后的那一句人话。

    弹窗推迟到事件循环下一轮：异常发生时界面状态未知，当场弹可能有半个
    控件是悬空的。
    """
    global _crash_reported
    if _crash_reported:
        return
    _crash_reported = True

    box = QMessageBox(window)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("出现了一个未处理的错误")
    box.setText("程序遇到一个未处理的错误，已经记录到日志。")
    box.setInformativeText(
        f"{message[:240]}\n\n"
        "功能可能不完整，建议重启程序。日志里能查到完整堆栈。"
    )
    open_btn = box.addButton("打开日志目录", QMessageBox.ActionRole)
    box.addButton("知道了", QMessageBox.AcceptRole)
    box.exec()
    if box.clickedButton() is open_btn:
        try:
            os.startfile(str(applog.log_path().parent))
        except OSError:
            pass



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
        applog.logger("app").warning("设置 AppUserModelID 失败", exc_info=True)

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

    # 装上之后，任何未捕获异常都会落盘，并给用户一次「打开日志目录」的机会
    applog.install_excepthook(
        lambda message: QTimer.singleShot(0, lambda: _report_crash(window, message))
    )

    start_hidden = "--minimized" in sys.argv or settings.start_minimized
    if not start_hidden:
        window.show()
    else:
        window.hide()

    return app.exec()


if __name__ == "__main__":
    # 日志要尽早起来：单实例锁没抢到、GUI 起不来这类问题也该有迹可循
    applog.setup(version.text())
    applog.capture_stderr()

    # 排查用的命令行模式：不抢单实例锁，否则会被已运行的实例直接吞掉
    if "--check-update" in sys.argv:
        sys.exit(_check_update_cli())

    # 提权重启时旧进程可能还在收尾，多等几秒再放弃
    wait = 6.0 if "--elevated" in sys.argv else 0.8
    if not single_instance.acquire(wait):
        applog.logger("app").info("已有实例在运行，唤到前台后退出")
        # 已有实例：把它的窗口叫到前台，而不是让用户以为程序没反应
        single_instance.activate_existing()
        sys.exit(0)
    try:
        code = main()
    except BaseException:
        # 走到这里说明连 Qt 事件循环都没进去，hook 还没装上
        applog.logger("crash").exception("启动阶段失败")
        raise
    finally:
        # 无论正常退出还是异常，都要把锁交出来
        single_instance.release()
    applog.logger("app").info("正常退出，code=%s", code)
    sys.exit(code)
