"""系统瘦身页截图。

默认走真实数据（开关状态直接读系统、体积真扫），只把「已关闭」凑几个出来 ——
开发机上这些项通常全是默认开启，截出来一排开关全绿，看不出关闭态长什么样。

用法：
    python tools/preview_slim.py [out.png] [light|dark|real]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core import config
from ui import theme_dark
from ui.main_window import MainWindow

# 只改「显示」，不真写注册表：截图脚本不能顺手把机器设置改了
FAKE_OFF = {"update_auto", "update_share", "telemetry", "error_report", "ad_id"}


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "preview_slim.png")
    mode = sys.argv[2] if len(sys.argv) > 2 else "dark"
    real = mode == "real"

    app = QApplication(sys.argv[:1])
    settings = config.Settings.load()
    theme_dark.set_accent(settings.accent or None)
    theme_dark.set_theme("light" if mode == "light" else "dark")
    app.setStyleSheet(theme_dark.app_stylesheet())
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow(settings)
    width = int(os.environ.get("PREVIEW_W", 1100))
    window.resize(width, int(os.environ.get("PREVIEW_H", 720)))
    window.show()
    window._theme_watch.stop()
    window.switch_page("slim")

    page = window.page_slim

    def apply_fake() -> None:
        for key in FAKE_OFF:
            row = page._rows.get(key)
            if row is not None:
                row.set_state(False)
        page._refresh_summary()
        page.lbl_stat.setText("已关闭 5 / 9 项 · 可回收 6.4 GB")
        QTimer.singleShot(300, shot)

    def shot() -> None:
        window.grab().save(str(out))
        print("已保存", out, window.size().width(), window.size().height())
        window._shutdown()
        app.quit()

    QTimer.singleShot(2500 if real else 1400, shot if real else apply_fake)
    app.exec()


if __name__ == "__main__":
    main()
