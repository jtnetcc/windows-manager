"""清理页「还有几个删不掉」的三种状态截图。

清理真跑一遍才能看到这一屏，这里直接往 store 里塞假数据 + 假的占用者，
只为检查排版和按钮文案；查占用/关程序的逻辑要真起一个占着文件的进程才能验，
不在这个脚本里。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core import config, filelock
from core.cleaner import FileItem, update_backlog
from ui import theme_dark
from ui.main_window import MainWindow


def remaining_items() -> list[FileItem]:
    return [
        FileItem(r"C:\Users\Jt\AppData\Local\Google\Chrome\User Data\Default\Cache\f_0000a1", 320_000_000, "chrome_cache", "浏览器缓存"),
        FileItem(r"C:\Users\Jt\AppData\Local\Google\Chrome\User Data\Default\Cache\f_0000a2", 180_000_000, "chrome_cache", "浏览器缓存"),
        FileItem(r"C:\Users\Jt\AppData\Local\Microsoft\Edge\User Data\Default\Cache\f_0000b1", 96_000_000, "edge_cache", "浏览器缓存"),
    ]


def main() -> None:
    app = QApplication(sys.argv[:1])
    settings = config.Settings.load()
    theme_dark.set_accent(settings.accent or None)
    theme_dark.set_theme("light")
    app.setStyleSheet(theme_dark.app_stylesheet())
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow(settings)
    window.resize(1100, 720)
    window.show()
    window.switch_page("clean")
    page = window.page_clean
    # 截图不触发真实的占用查询（假路径查不到，还会把注入的占用者冲掉）
    page._check_locks = lambda: None

    def capture(widget, name: str, delay: int = 500) -> None:
        app.processEvents()
        widget.grab().save(name)
        print(f"已保存 {name}")
        QTimer.singleShot(delay, advance)

    step = [0]

    def advance() -> None:
        step[0] += 1
        if step[0] == 1:
            finish_clean()
            return
        if step[0] == 2:
            # 查出被 Chrome / Edge 占着
            page._locks = [
                filelock.Holder(pid=1, name="Google Chrome", pids=[1], closable=True),
                filelock.Holder(pid=2, name="Microsoft Edge", pids=[2], closable=True),
            ]
            page._checking_locks = False
            page._update_detail()
            page._sync_buttons()
            capture(window, "preview_lock_done.png")
            return
        if step[0] == 3:
            box, _go = page._unlock_box(page._closable_locks())
            box.show()
            box.grab().save("preview_lock_dialog.png")
            print("已保存 preview_lock_dialog.png")
            box.close()
            capture(window, "preview_lock_done.png", 200)
            return
        if step[0] == 4:
            # 只有系统程序占着：不提供「关闭并清理」，退回重试
            page._locks = [
                filelock.Holder(pid=3, name="Windows 资源管理器", pids=[3], closable=False)
            ]
            page._update_detail()
            page._sync_buttons()
            capture(window, "preview_lock_blocked.png")
            return

        window._shutdown()
        app.quit()

    def finish_clean() -> None:
        items = remaining_items()
        page._phase = "ready"
        window.store.set_junk_items(items, sum(i.size for i in items), update_backlog(items))
        page._last_clean = (3210, 3, 3_540_000_000)
        page._pending_done = True
        window.store.set_junk_items(items, sum(i.size for i in items), update_backlog(items))
        QTimer.singleShot(300, lambda: capture(window, "preview_lock_clean.png"))

    QTimer.singleShot(600, advance)
    app.exec()


if __name__ == "__main__":
    main()
