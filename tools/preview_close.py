"""关闭确认框预览：浅色 / 深色各一张。

用法：python tools/preview_close.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

from ui import theme_dark
from ui.main_window import CloseChoiceDialog


def main() -> None:
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)

    theme = sys.argv[1] if len(sys.argv) > 1 else "light"
    theme_dark.set_theme(theme)
    app.setStyleSheet(theme_dark.app_stylesheet())

    dialog = CloseChoiceDialog()
    dialog.show()
    app.processEvents()

    out = Path(f"preview_close_{theme}.png")
    dialog.grab().save(str(out))
    print(f"{out} {dialog.width()}x{dialog.height()}")
    dialog.close()


if __name__ == "__main__":
    main()
