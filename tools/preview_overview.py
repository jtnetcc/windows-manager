"""总览页渲染：浅色/深色各一张，再加一张最小窗口尺寸。

改版总览之后用它自检：卡片有没有互相挤、右列两张短卡在 640 高度下
会不会把最后一行列表裁掉。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core import config
from ui import theme_dark
from ui.main_window import MainWindow


SHOTS = [
    ("light", 1100, 720, "preview_overview_light.png"),
    ("dark", 1100, 720, "preview_overview_dark.png"),
    ("light", 980, 640, "preview_overview_min.png"),
]


def main() -> None:
    app = QApplication(sys.argv[:1])
    settings = config.Settings.load()
    theme_dark.set_accent(settings.accent or None)
    theme_dark.set_theme("light")
    app.setStyleSheet(theme_dark.app_stylesheet())
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow(settings)
    window.resize(1100, 720)
    window.show()
    window.switch_page("monitor")
    window.page_monitor.set_segment("overview")

    state = {"i": -1}

    def step() -> None:
        state["i"] += 1
        if state["i"] >= len(SHOTS):
            window._shutdown()
            app.quit()
            return
        theme, width, height, name = SHOTS[state["i"]]
        if theme_dark.current_mode() != theme:
            # 主窗口有个 4 秒一次的「跟随系统」看门狗，会把主题拽回去，
            # 截图脚本里得先停掉它
            window._theme_watch.stop()
            theme_dark.set_theme(theme)
            window._rebuild_for_theme()
            window.switch_page("monitor")
            window.page_monitor.set_segment("overview")
        window.resize(width, height)
        QTimer.singleShot(1500, lambda: shot(name))

    def shot(name: str) -> None:
        window.grab().save(name)
        print(f"已保存 {name}")
        step()

    # 等三轮采样，曲线里才有线，内存构成的数字也才是真值
    QTimer.singleShot(4500, step)
    app.exec()


if __name__ == "__main__":
    main()
