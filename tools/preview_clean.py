"""垃圾清理页截图：空态 / 扫描结果 / 清理完成 三种状态各一张。

扫描和删除要真跑几万个文件才能看到界面，这里直接往 store 里塞假数据，
只为检查排版，不验证业务逻辑。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core import config
from core.cleaner import FileItem, builtin_rules, update_backlog
from core.store import JunkState
from ui import theme_dark
from ui.main_window import MainWindow

# 规则 id -> (文件数, 总体积)，按真实场景捏的量级
SAMPLE = {
    "user_temp": (326, 1_240_000_000),
    "crash_dumps": (4, 630_000_000),
    "edge_cache": (2104, 720_000_000),
    "chrome_cache": (3418, 1_120_000_000),
    "firefox_cache": (860, 320_000_000),
    "thumbnails": (12, 180_000_000),
    "windows_temp": (88, 240_000_000),
    "windows_logs": (30, 90_000_000),
    "prefetch": (42, 24_000_000),
    "windows_update_download": (120, 2_400_000_000),
    "wer_reports": (6, 32_000_000),
}


def fake_items() -> list[FileItem]:
    items: list[FileItem] = []
    for rule in builtin_rules():
        if rule.id not in SAMPLE:
            continue
        count, size = SAMPLE[rule.id]
        per = max(1, size // count)
        for index in range(count):
            items.append(
                FileItem(
                    f"C:\\fake\\{rule.id}\\file_{index:05d}.tmp",
                    per,
                    rule.id,
                    rule.category,
                )
            )
    return items


def main() -> None:
    app = QApplication(sys.argv[:1])
    settings = config.Settings.load()
    theme_dark.set_accent(settings.accent or None)
    theme_dark.set_theme(settings.theme)
    app.setStyleSheet(theme_dark.app_stylesheet())
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow(settings)
    window.resize(1100, 720)
    window.show()
    window.switch_page("clean")
    page = window.page_clean

    steps = [0]

    def capture(name: str) -> None:
        # 先把这一轮布局/重绘跑完，否则截到的是上一帧的标签
        app.processEvents()
        window.grab().save(name)
        print(f"已保存 {name}")
        steps[0] += 1
        QTimer.singleShot(500, advance)

    def advance() -> None:
        if steps[0] == 1:
            items = fake_items()
            window.store.junk = JunkState(
                items=items,
                total=sum(i.size for i in items),
                update_backlog=update_backlog(items),
                social=sum(i.size for i in items if i.category == "社交缓存"),
                scanned_at=time.time(),
            )
            window.store.junk_changed.emit()
            capture("preview_clean_result.png")
            return

        if steps[0] == 2:
            # 模拟清理完成：删掉大部分，剩 3 个被占用的
            remaining = [
                FileItem("C:\\fake\\lock\\a.tmp", 12_000_000, "user_temp", "临时文件")
            ]
            page._last_clean = (len(page._items) - 1, 3, sum(i.size for i in page._items))
            page._pending_done = True
            window.store.set_junk_items(
                remaining,
                sum(i.size for i in remaining),
                update_backlog(remaining),
            )
            capture("preview_clean_done.png")
            return

        window._shutdown()
        app.quit()

    QTimer.singleShot(800, lambda: capture("preview_clean.png"))
    app.exec()


if __name__ == "__main__":
    main()
