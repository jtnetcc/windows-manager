"""逐页截图，用于检查每个分区的布局。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core import config
from ui import theme_dark
from ui.main_window import NAV_ITEMS, MainWindow


def main() -> None:
    app = QApplication(sys.argv[:1])
    settings = config.Settings.load()
    theme_dark.set_accent(settings.accent or None)
    theme_dark.set_theme(settings.theme)
    app.setStyleSheet(theme_dark.app_stylesheet())
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow(settings)
    window.resize(1040, 668)
    window.show()

    keys = [k for k, _ in NAV_ITEMS]
    state = {"i": 0}

    def capture() -> None:
        if state["i"] >= len(keys):
            window._shutdown()
            app.quit()
            return
        key = keys[state["i"]]
        window.grab().save(f"preview_{key}.png")
        print(f"已保存 preview_{key}.png")
        state["i"] += 1
        QTimer.singleShot(400, advance)

    def advance() -> None:
        if state["i"] >= len(keys):
            window._shutdown()
            app.quit()
            return
        window.switch_page(keys[state["i"]])
        QTimer.singleShot(900, capture)

    QTimer.singleShot(5000, advance)
    app.exec()


if __name__ == "__main__":
    main()
