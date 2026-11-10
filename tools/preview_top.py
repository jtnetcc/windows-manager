"""抓取「页面顶栏 + 页面工具条」两行，检查按钮排布。

用法：python tools/preview_top.py <page_key> <out.png>
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


def main() -> None:
    key = sys.argv[1] if len(sys.argv) > 1 else "registry"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(f"preview_top_{key}.png")
    dark = len(sys.argv) > 3 and sys.argv[3] == "dark"

    app = QApplication(sys.argv[:1])
    settings = config.Settings.load()
    theme_dark.set_accent(settings.accent or None)
    theme_dark.set_theme("dark" if dark else "light")
    app.setStyleSheet(theme_dark.app_stylesheet())
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow(settings)
    import os

    window.resize(
        int(os.environ.get("PREVIEW_W", 1060)),
        int(os.environ.get("PREVIEW_H", 690)),
    )
    window.show()
    window._theme_watch.stop()

    # 塞一份「可释放」数据，让顶栏按钮都露出来
    from core import cleaner

    items = [
        cleaner.FileItem(
            path=f"C:/Windows/Temp/stub_{i}.tmp",
            size=120 * 1024 * 1024,
            rule_id="temp_user",
            category="系统缓存",
        )
        for i in range(8)
    ]
    window.store.set_junk_items(items, sum(i.size for i in items), 0)
    _ = time

    def capture() -> None:
        window.switch_page(key)
        QTimer.singleShot(500, lambda: shot())

    def shot() -> None:
        pix = window.grab()
        height = pix.height() if len(sys.argv) > 4 and sys.argv[4] == "full" else 240
        pix.copy(0, 0, pix.width(), height).save(str(out))
        print("已保存", out)
        window._shutdown()
        app.quit()

    QTimer.singleShot(1200, capture)
    app.exec()


if __name__ == "__main__":
    main()
