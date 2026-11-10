"""全面体检页整窗截图，用来检查下排「磁盘空间 / 实时负载」的高度与排版。

体检数据靠 `start_probe()` 真跑一次系统探测（磁盘 SMART + 启动项），
几秒内出结果；垃圾/重复文件用假数据补齐，免得评分只有三分之一。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core import cleaner, config, dup
from ui import theme_dark
from ui.main_window import MainWindow


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "preview_health.png")
    dark = len(sys.argv) > 2 and sys.argv[2] == "dark"

    app = QApplication(sys.argv[:1])
    settings = config.Settings.load()
    theme_dark.set_accent(settings.accent or None)
    theme_dark.set_theme("dark" if dark else "light")
    app.setStyleSheet(theme_dark.app_stylesheet())
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow(settings)
    window.resize(int(os.environ.get("PREVIEW_W", 1100)), int(os.environ.get("PREVIEW_H", 720)))
    window.show()
    window._theme_watch.stop()

    items = [
        cleaner.FileItem(
            path=f"C:/Users/Stub/AppData/Local/Temp/cache_{i}.tmp",
            size=68 * 1024 * 1024,
            rule_id="temp_user",
            category="系统缓存",
        )
        for i in range(24)
    ]
    window.store.set_junk_items(items, sum(i.size for i in items), 0)
    # 故意造一组超标的重复文件：截图要能同时看到「处理 ›」和不达标行
    window.store.set_dup_groups(
        [
            dup.DupGroup(
                size=int(2.1 * 1024 ** 3),
                files=["C:/a/setup.iso", "C:/b/setup.iso", "D:/bak/setup.iso"],
            )
        ]
    )

    def start() -> None:
        window.switch_page("health")
        window.page_health.start_probe()
        QTimer.singleShot(4000, shot)

    def shot() -> None:
        window.grab().save(str(out))
        print("已保存", out, window.size().width(), window.size().height())
        window._shutdown()
        app.quit()

    QTimer.singleShot(900, start)
    app.exec()


if __name__ == "__main__":
    main()
