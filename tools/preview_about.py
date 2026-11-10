"""关于页截图。

除了看排版，还顺手报一下侧栏需要多高 —— 底部多了一项「关于软件」，
窗口最小高度 640 时会不会挤掉权限徽标，靠这个数字判断。

用法：
    python tools/preview_about.py [out.png] [light|dark] [高]
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


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "preview_about.png")
    mode = sys.argv[2] if len(sys.argv) > 2 else "dark"
    height = int(sys.argv[3]) if len(sys.argv) > 3 else 720

    app = QApplication(sys.argv[:1])
    settings = config.Settings.load()
    theme_dark.set_accent(settings.accent or None)
    theme_dark.set_theme("light" if mode == "light" else "dark")
    app.setStyleSheet(theme_dark.app_stylesheet())
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow(settings)
    window.resize(1100, height)
    window.show()
    window._theme_watch.stop()
    window.switch_page("about")

    def shot() -> None:
        # 侧栏不是滚动区，底部多了「关于软件」这一项，看看权限徽标有没有被
        # 挤出窗口：拿最后两项的底边位置和窗口高度比一下
        nav = window._nav["about"]
        nav_bottom = nav.mapTo(window, nav.rect().bottomLeft()).y()
        admin = window.btn_admin
        admin_bottom = admin.mapTo(window, admin.rect().bottomLeft()).y()
        print(
            f"窗口 {window.height()}px · 关于项底边 {nav_bottom}px"
            f" · 权限按钮底边 {admin_bottom}px"
        )
        window.grab().save(str(out))
        print("已保存", out)
        window._shutdown()
        app.quit()

    QTimer.singleShot(1200, shot)
    app.exec()


if __name__ == "__main__":
    main()
