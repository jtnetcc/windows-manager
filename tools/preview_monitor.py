"""监控页逐段截图：一个入口里的 6 个分段各截一张。

用来检查分段器排版、子页嵌进壳之后有没有被压扁。
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
from ui.pages_monitor import SEGMENTS


def main() -> None:
    app = QApplication(sys.argv[:1])
    # 和 main.py 一致：先按配置定主题，否则截图里会是模块默认的深色，
    # 和用户实际看到的不一样
    settings = config.Settings.load()
    theme_dark.set_accent(settings.accent or None)
    theme_dark.set_theme(settings.theme)
    app.setStyleSheet(theme_dark.app_stylesheet())
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow(settings)
    window.resize(1100, 720)
    window.show()
    window.switch_page("monitor")

    keys = [key for key, _text in SEGMENTS]
    state = {"i": 0}

    def capture() -> None:
        if state["i"] >= len(keys):
            window._shutdown()
            app.quit()
            return
        key = keys[state["i"]]
        window.grab().save(f"preview_monitor_{key}.png")
        print(f"已保存 preview_monitor_{key}.png")
        state["i"] += 1
        QTimer.singleShot(300, advance)

    def advance() -> None:
        if state["i"] >= len(keys):
            window._shutdown()
            app.quit()
            return
        window.page_monitor.set_segment(keys[state["i"]])
        QTimer.singleShot(1200, capture)

    # 等两轮采样，图表里才有线
    QTimer.singleShot(4000, advance)
    app.exec()


if __name__ == "__main__":
    main()
