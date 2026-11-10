"""托盘右键菜单自检：结构、状态行刷新、自启开关（会还原原值）。

用法：python tools/check_tray.py [out.png]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core import cleaner, config
from ui import theme_dark
from ui.main_window import MainWindow


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "preview_tray.png")
    light = len(sys.argv) > 2 and sys.argv[2] == "light"

    app = QApplication(sys.argv[:1])
    settings = config.Settings.load()
    theme_dark.set_accent(settings.accent or None)
    theme_dark.set_theme("light" if light else "dark")
    app.setStyleSheet(theme_dark.app_stylesheet())
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow(settings)
    window.show()
    window._theme_watch.stop()

    items = [
        cleaner.FileItem(
            path=f"C:/Users/Stub/AppData/Local/Temp/cache_{i}.tmp",
            size=48 * 1024 * 1024,
            rule_id="temp_user",
            category="系统缓存",
        )
        for i in range(20)
    ]
    window.store.set_junk_items(items, sum(i.size for i in items), 0)

    def report() -> None:
        menu = window._tray_menu
        window._sync_tray_menu()
        print("--- 菜单结构 ---")
        for action in menu.actions():
            if action.isSeparator():
                print("  ----")
                continue
            mark = "✓" if action.isChecked() else " "
            state = "可用" if action.isEnabled() else "禁用"
            bold = " [默认]" if action is menu.defaultAction() else ""
            print(f"  [{mark}] {action.text()}  ({state}){bold}")
        print("状态行:", window._tray_state.text())

        # 自启开关往返一次，确认注册表与设置页同步后能还原
        before = config.is_autostart_enabled()
        window._tray_startup.setChecked(not before)
        mid = config.is_autostart_enabled()
        synced = window.page_settings.chk_startup.isChecked()
        window._tray_startup.setChecked(before)
        after = config.is_autostart_enabled()
        print(
            f"自启：原 {before} → 切 {mid}（设置页 {synced}）→ 还原 {after}"
        )
        assert mid == (not before), "托盘自启没写进注册表"
        assert synced == mid, "设置页开关没跟上"
        assert after == before, "还原失败"

        # 截图：菜单在这台机器上不 exec，直接 grab 一张
        menu.adjustSize()
        menu.grab().save(str(out))
        print("已保存", out, menu.size().width(), menu.size().height())

        # 勾选态单独截一张，确认指示器没和文字挤在一起
        from PySide6.QtCore import QSignalBlocker

        with QSignalBlocker(window._tray_startup):
            window._tray_startup.setChecked(True)
        checked_out = out.with_name(out.stem + "_checked" + out.suffix)
        menu.adjustSize()
        menu.grab().save(str(checked_out))
        print("已保存", checked_out)
        with QSignalBlocker(window._tray_startup):
            window._tray_startup.setChecked(before)

        # 页面是按需建的（启动只造首屏），所以这里把全部导航页都过一遍：
        # 托盘、快捷键、体检跳转指过去时都得真能建出页面
        from ui.main_window import NAV_ITEMS

        for key, _title in NAV_ITEMS:
            assert window._ensure_page(key) is not None, f"页面建不出来：{key}"
        print(f"按需建页：{len(NAV_ITEMS)} 个导航项全部可建 ✓")

        window._shutdown()
        app.quit()

    QTimer.singleShot(1200, report)
    app.exec()

    print("OK")


if __name__ == "__main__":
    main()
