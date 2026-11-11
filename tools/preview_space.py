"""磁盘空间页渲染：浅色 / 深色 / 最小窗口。

扫描要十几秒，脚本先等它跑完再截图 —— 截「统计中」的进度条没有意义，
要看的是卡片排得对不对、目录行有没有被裁。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core import config
from ui import theme_dark
from ui.main_window import MainWindow


SHOTS = [
    ("light", 1100, 720, "preview_space_light.png"),
    ("dark", 1100, 720, "preview_space_dark.png"),
    ("light", 980, 640, "preview_space_min.png"),
]

WAIT_MS = 180_000


def main() -> None:
    app = QApplication(sys.argv[:1])
    settings = config.Settings.load()
    settings.theme = "light"
    theme_dark.set_accent(settings.accent or None)
    theme_dark.set_theme("light")
    app.setStyleSheet(theme_dark.app_stylesheet())
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow(settings)
    window.resize(1100, 720)
    window.show()
    window.switch_page("bigfile")

    state = {"i": 0, "deadline": time.monotonic() + WAIT_MS / 1000}
    view = None

    def page():
        return window._pages.get("bigfile")

    def wait_ready() -> None:
        nonlocal view
        page_obj = page()
        if page_obj is None:
            QTimer.singleShot(200, wait_ready)
            return
        view = page_obj.analysis
        if view._busy and time.monotonic() < state["deadline"]:
            QTimer.singleShot(400, wait_ready)
            return
        print(f"扫描结束：{view._result.seconds:.0f}s" if view._result else "扫描未完成")
        QTimer.singleShot(400, shot_current)

    def shot_current() -> None:
        if state["i"] >= len(SHOTS):
            files_step()
            return
        theme, width, height, name = SHOTS[state["i"]]
        state["i"] += 1
        if theme_dark.current_mode() != theme:
            window._theme_watch.stop()
            theme_dark.set_theme(theme)
            window._rebuild_for_theme()
            window.switch_page("bigfile")
            segment = "analysis"
            page_obj = page()
            if page_obj is not None:
                page_obj.set_segment(segment)
        window.resize(width, height)
        QTimer.singleShot(900, lambda: shot(name))

    def shot(name: str) -> None:
        window.grab().save(name)
        print(f"已保存 {name}")
        QTimer.singleShot(200, shot_current)

    def files_step() -> None:
        """顺手把「大文件」分段也截一张：默认范围改成系统盘之后，
        要确认休眠/分页文件确实被标灰、不能勾。"""
        page_obj = page()
        if page_obj is None:
            window._shutdown()
            app.quit()
            return
        page_obj.set_segment("files")
        QTimer.singleShot(300, lambda: page_obj.files.start_scan())
        QTimer.singleShot(800, wait_files)

    def wait_files() -> None:
        if window.tasks.is_busy("bigfile") and time.monotonic() < state["deadline"]:
            QTimer.singleShot(500, wait_files)
            return
        QTimer.singleShot(800, shot_files)

    def shot_files() -> None:
        window.grab().save("preview_space_files.png")
        print("已保存 preview_space_files.png")
        window._shutdown()
        app.quit()

    QTimer.singleShot(3000, wait_ready)
    app.exec()


if __name__ == "__main__":
    main()
