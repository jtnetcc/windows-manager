"""逐套皮肤截图，用于检查配色和控件渲染。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core import config
from ui import theme_dark
from ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)

    settings = config.Settings.load()
    theme_dark.set_theme(theme_dark.DEFAULT_THEME)
    app.setStyleSheet(theme_dark.app_stylesheet())

    window = MainWindow(settings)
    window.resize(1040, 668)
    window.show()

    keys = list(theme_dark.THEME_ORDER)
    state = {"i": 0}

    def step() -> None:
        if state["i"] >= len(keys):
            window.switch_page("settings")
            QTimer.singleShot(700, shot_settings)
            return
        key = keys[state["i"]]
        window.apply_theme(key)
        state["i"] += 1
        QTimer.singleShot(900, lambda: shot_theme(key))

    def shot_theme(key: str) -> None:
        window.grab().save(f"preview_theme_{key}.png")
        print(f"已保存 preview_theme_{key}.png")
        step()

    def shot_settings() -> None:
        window.grab().save("preview_settings.png")
        print("已保存 preview_settings.png")
        window.switch_page("monitor")
        window.page_monitor.set_segment("process")
        QTimer.singleShot(900, shot_process)

    def shot_process() -> None:
        window.grab().save("preview_process.png")
        print("已保存 preview_process.png")
        window._shutdown()
        app.quit()

    QTimer.singleShot(5000, step)
    app.exec()


if __name__ == "__main__":
    main()
