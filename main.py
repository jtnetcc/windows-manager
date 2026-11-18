"""Windows系统管家 —— 启动入口。

用法：
    python main.py              # 正常启动
    python main.py --minimized  # 开机自启时静默启动到托盘
    python main.py --elevated   # 提权重启（内部使用，会延长单实例等待）
    python main.py --check-update  # 只跑一次检查更新并打印结果（排查用）
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _require_windows() -> None:
    """非 Windows 平台在这里就停下，给一句人话。

    为什么放在最前面：这个程序的每一项功能都长在 Windows 上 —— 注册表
    （winreg）、内存统计（GlobalMemoryStatusEx）、磁盘健康（DeviceIoControl）、
    启动项（schtasks / 注册表 Run 键）、回收站（SHQueryRecycleBin）、
    单实例（命名互斥体）。换到别的系统上，连 `import core.winmem` 都会失败，
    直接抛 ImportError 用户只会看到「程序意外退出」。

    这里不做任何「兼容层」或「降级模式」：本软件只发布 Windows 版。
    """
    if sys.platform == "win32":
        return

    print(
        "Windows系统管家 只能在 Windows 上运行。\n"
        f"当前系统：{sys.platform}\n"
        "它依赖注册表、WMI、DeviceIoControl 等 Windows 专有接口。",
        file=sys.stderr,
    )
    raise SystemExit(2)


_require_windows()

import ctypes  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from core import applog, config, single_instance, version  # noqa: E402
from ui import theme_dark  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

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
    # 启动分段计时：卡顿投诉里最缺的就是「慢在哪一段」
    t_start = time.perf_counter()

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
    applog.logger("app").info(
        "启动：Qt 就绪 %.0f ms", (time.perf_counter() - t_start) * 1000
    )

    settings = config.Settings.load()
    settings.autostart = config.is_autostart_enabled()

    # 界面皮肤。命令行优先：`--skin air` 可以先看效果，不动已保存的设置，
    # 关掉窗口再启动就回到 classic（等于一次完整的回滚）。
    skin = settings.skin or "classic"
    for index, arg in enumerate(sys.argv):
        if arg == "--skin" and index + 1 < len(sys.argv):
            skin = sys.argv[index + 1]
        elif arg.startswith("--skin="):
            skin = arg.split("=", 1)[1]
    theme_dark.set_skin(skin)
    applog.logger("app").info("界面皮肤：%s", skin)

    # 样式表必须在建窗口之前套上，否则首帧会闪一下默认配色
    theme_dark.set_accent(settings.accent or None)
    theme_dark.set_theme(settings.theme)
    app.setStyleSheet(theme_dark.app_stylesheet())

    window = MainWindow(settings)
    applog.logger("app").info(
        "启动：窗口构建完成 %.0f ms", (time.perf_counter() - t_start) * 1000
    )

    # 装上之后，任何未捕获异常都会落盘，并给用户一次「打开日志目录」的机会
    applog.install_excepthook(
        lambda message: QTimer.singleShot(0, lambda: _report_crash(window, message))
    )

    start_hidden = "--minimized" in sys.argv or settings.start_minimized
    if not start_hidden:
        window.show()
    else:
        window.hide()
    applog.logger("app").info(
        "启动：窗口可见 %.0f ms", (time.perf_counter() - t_start) * 1000
    )

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
