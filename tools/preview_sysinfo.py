"""系统信息页渲染：浅色 / 深色 / 最小窗口。

新加的「激活与授权」卡在页面最下方，窗口不够高会被滚出视野，
所以先截一张高的看整体，再截一张最小窗口看会不会挤。
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
    ("light", 1180, 880, "preview_sysinfo_light.png"),
    ("dark", 1180, 880, "preview_sysinfo_dark.png"),
    ("light", 980, 660, "preview_sysinfo_min.png"),
]

WAIT_MS = 20_000


def main() -> None:
    app = QApplication(sys.argv[:1])
    settings = config.Settings.load()
    settings.theme = "light"
    theme_dark.set_accent(settings.accent or None)
    theme_dark.set_theme("light")
    app.setStyleSheet(theme_dark.app_stylesheet())
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow(settings)
    window.resize(1180, 880)
    window.show()
    window.switch_page("sysinfo")

    state = {"i": 0, "deadline": time.monotonic() + WAIT_MS}

    def page():
        return window._pages.get("sysinfo")

    def wait_ready() -> None:
        obj = page()
        if obj is None or obj._activation_loading:
            if time.monotonic() < state["deadline"]:
                QTimer.singleShot(200, wait_ready)
                return
            print("激活信息未在限期内返回")
        act = page()._activation
        print("激活：", act)
        QTimer.singleShot(300, shot_current)

    def shot_current() -> None:
        if state["i"] >= len(SHOTS):
            window._shutdown()
            app.quit()
            return
        theme, width, height, name = SHOTS[state["i"]]
        state["i"] += 1
        if theme_dark.current_mode() != theme:
            window._theme_watch.stop()
            theme_dark.set_theme(theme)
            window._rebuild_for_theme()
            window.switch_page("sysinfo")
        window.resize(width, height)
        QTimer.singleShot(1400, lambda: shot(name))

    def shot(name: str) -> None:
        window.grab().save(name)
        print(f"已保存 {name}")
        QTimer.singleShot(200, shot_current)

    QTimer.singleShot(1200, wait_ready)
    app.exec()


if __name__ == "__main__":
    main()
