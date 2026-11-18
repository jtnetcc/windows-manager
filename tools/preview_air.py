"""留白皮肤截图：一次出几张，方便对比要不要留下。

用法：

    python tools/preview_air.py                 # 默认 dark 对照 + air
    python tools/preview_air.py health monitor  # 指定页面

输出到 docs/preview/air-<page>.png。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core import config  # noqa: E402
from ui import theme_dark  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

PAGES = sys.argv[1:] or ["health", "monitor", "software"]


def main() -> None:
    app = QApplication(sys.argv[:1])
    settings = config.Settings.load()

    theme_dark.set_skin("air")
    theme_dark.set_theme("light")
    app.setStyleSheet(theme_dark.app_stylesheet())

    window = MainWindow(settings)
    window.resize(1180, 760)
    window.show()

    out_dir = ROOT / "docs" / "preview"
    out_dir.mkdir(parents=True, exist_ok=True)

    state = {"index": 0}

    def shot() -> None:
        index = state["index"]
        if index >= len(PAGES):
            window.close()
            app.quit()
            return
        page = PAGES[index]
        window.switch_page(page)
        app.processEvents()
        QTimer.singleShot(900, lambda: capture(page))

    def capture(page: str) -> None:
        target = out_dir / f"air-{page}.png"
        window.grab().save(str(target))
        print("已保存", target)
        state["index"] += 1
        QTimer.singleShot(200, shot)

    QTimer.singleShot(1200, shot)
    app.exec()


if __name__ == "__main__":
    main()
