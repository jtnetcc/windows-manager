"""重复文件 / 大文件页截图（真实扫描）。"""

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
    theme_dark.set_theme(settings.theme)
    app.setStyleSheet(theme_dark.app_stylesheet())

    window = MainWindow(settings)
    window.resize(1040, 668)
    window.show()

    def start_dup() -> None:
        window.switch_page("dup")
        QTimer.singleShot(800, window.page_dup.start_scan)
        QTimer.singleShot(40000, lambda: save("dup", start_big))

    def start_big() -> None:
        window.switch_page("bigfile")
        QTimer.singleShot(800, window.page_bigfile.start_scan)
        QTimer.singleShot(20000, lambda: save("bigfile", finish))

    def save(name: str, then) -> None:
        window.grab().save(f"preview_{name}.png")
        print(f"已保存 preview_{name}.png")
        then()

    def finish() -> None:
        window._shutdown()
        app.quit()

    QTimer.singleShot(3000, start_dup)
    app.exec()


if __name__ == "__main__":
    main()
