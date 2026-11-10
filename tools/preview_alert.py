"""验证告警 toast 的样式与位置（不依赖真实阈值触发）。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core import config
from core.alerts import LEVEL_CRIT, LEVEL_WARN, Alert
from ui import theme_dark
from ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv[:1])
    app.setStyleSheet(theme_dark.app_stylesheet())
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow(config.Settings.load())
    window.resize(1040, 668)
    window.show()

    def fire_warn() -> None:
        window._show_alert(
            Alert(
                key="cpu",
                level=LEVEL_WARN,
                title="CPU 持续高负载",
                message="已持续 1 分钟 高于 90%，当前 95%（7/8 个核心超过阈值）",
                value=95.0,
                timestamp=time.time(),
            )
        )
        QTimer.singleShot(700, shot_warn)

    def shot_warn() -> None:
        window.grab().save("preview_alert_warn.png")
        print("已保存 preview_alert_warn.png")
        window._show_alert(
            Alert(
                key="memory",
                level=LEVEL_CRIT,
                title="内存占用偏高",
                message="已用 96%（15.4 GB / 16.0 GB），可用仅 640 MB",
                value=96.0,
                timestamp=time.time(),
            )
        )
        QTimer.singleShot(700, shot_crit)

    def shot_crit() -> None:
        window.grab().save("preview_alert_crit.png")
        print("已保存 preview_alert_crit.png")
        window._shutdown()
        app.quit()

    QTimer.singleShot(4500, fire_warn)
    app.exec()


if __name__ == "__main__":
    main()
