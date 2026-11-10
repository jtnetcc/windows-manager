"""启动行为自检：首屏、默认主题、关于页、按需建页。

跑法：python tools/check_startup.py [--shot]
    --shot  额外存三张截图（首屏 / 偏好设置 / 监控-进程）

关注四件事：
1. 打开就是「全面体检」，不是性能监控；
2. 偏好设置里的主题默认是「跟随系统」；
3. 关于页能在 F1 到达，并且「免费开源」确实写在页面上；
4. 页面按需创建之后，每一页、每个监控分段都还能正常建出来，
   换肤重建后也还能建（懒加载记录要跟着清）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psutil
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from core import config
from ui import theme_dark as T
from ui.main_window import NAV_ITEMS, MainWindow

SHOT = "--shot" in sys.argv


def rss() -> float:
    return psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)

    # 用一份全新配置：不受这台机器上已有的 config.json 影响
    settings = config.Settings()
    assert settings.theme == "system", f"默认主题不是跟随系统：{settings.theme}"

    T.set_accent(None)
    T.set_theme(settings.theme)
    app.setStyleSheet(T.app_stylesheet())

    window = MainWindow(settings)
    window.resize(1040, 668)
    window.show()
    window._theme_watch.stop()

    print(f"启动内存 {rss():.1f} MB · 已建页面 {sorted(window._pages)}")

    # 1. 首屏
    assert window._current_page == "health", f"首屏不是全面体检：{window._current_page}"
    assert window.stack.currentWidget() is window._pages["health"]
    assert window._nav["health"]._active, "侧栏没选中全面体检"
    print("首屏 = 全面体检 ✓")

    # 2. 主题默认值
    page = window.page_settings
    assert page.combo_mode.currentData() == "system", (
        f"主题下拉默认值不对：{page.combo_mode.currentData()}"
    )
    assert "默认" in page.combo_mode.currentText()
    assert T.current_mode() == "system"
    print(f"主题下拉 = {page.combo_mode.currentText()} ✓")

    # 3. 关于页：F1 能到，且「免费开源」不是只写在文档里
    #    activateWindow 是异步的，得让事件循环跑一下，否则 QShortcut 还没接上焦点
    window.activateWindow()
    window.raise_()
    QTest.qWait(160)
    QTest.keyClick(window, Qt.Key_F1)
    app.processEvents()
    assert window._current_page == "about", f"F1 没打开关于页：{window._current_page}"
    texts = [lb.text() for lb in window._pages["about"].findChildren(QLabel)]
    assert any("免费" in t and "开源" in t for t in texts), "关于页没写「免费开源」"
    for word in ("永久免费", "开源可查", "无广告无捆绑", "不联网"):
        assert any(word in t for t in texts), f"关于页缺承诺：{word}"
    print("关于页 = 免费开源 ✓")

    # 下载渠道：填了 url 的必须显示出地址和按钮，没填的不该摆一颗点不动的按钮
    from ui.page_about import CHANNELS

    page = window._pages["about"]
    live = [c for c in CHANNELS if c.url]
    assert live, "没有任何可用的下载渠道"
    for channel in live:
        host = channel.url.split("://", 1)[-1].rstrip("/")
        assert any(host in t for t in texts), f"关于页没显示地址：{channel.name}"
    buttons = [btn.text() for btn in page.findChildren(QPushButton)]
    opened = buttons.count("打开")
    assert opened == len(live), f"「打开」按钮 {opened} 个，渠道 {len(live)} 个"
    print(f"下载渠道 {len(live)} 个（共 {len(CHANNELS)} 个位）· 地址与按钮齐全 ✓")
    window.switch_page("health")
    app.processEvents()

    if SHOT:
        window.grab().save(str(ROOT / "preview_start_health.png"))
        window.switch_page("settings")
        app.processEvents()
        window.grab().save(str(ROOT / "preview_start_settings.png"))
        window.switch_page("health")
        app.processEvents()

    report = {"err": []}

    def step(i: int = 0) -> None:
        keys = [key for key, _title in NAV_ITEMS]
        if i >= len(keys):
            segments(window)
            return
        key = keys[i]
        try:
            window.switch_page(key)
            assert window._pages.get(key) is not None
            assert window.stack.currentWidget() is window._pages[key]
            app.processEvents()
        except Exception as exc:  # noqa: BLE001
            report["err"].append(f"{key}: {exc}")
        QTimer.singleShot(60, lambda: step(i + 1))

    def segments(win) -> None:
        win.switch_page("monitor")
        app.processEvents()
        monitor = win._pages["monitor"]
        for key, _text in (("overview", ""), ("cpu", ""), ("memory", ""),
                           ("disk", ""), ("network", ""), ("process", "")):
            monitor.set_segment(key)
            app.processEvents()
            assert monitor.current_segment() == key, f"分段切不过去：{key}"
            assert key in monitor._pages, f"分段没建出来：{key}"
        print(f"监控 6 个分段全部可建 ✓（建完 {rss():.1f} MB）")
        if SHOT:
            win.grab().save(str(ROOT / "preview_start_process.png"))
        rebuild(win)

    def rebuild(win) -> None:
        """换肤重建：懒加载记录清空后，页面还得能重新建出来。"""
        win.switch_page("monitor")
        win._pages["monitor"].set_segment("process")
        win.apply_theme("dark" if T.current_mode() != "dark" else "light")
        app.processEvents()
        assert win._current_page == "monitor", win._current_page
        assert "monitor" in win._pages
        assert win._pages["monitor"].current_segment() == "process", "换肤后分段没还原"
        win.apply_theme("system")
        app.processEvents()
        print(f"换肤重建后仍按需建页 ✓（{rss():.1f} MB）")
        finish()

    def finish() -> None:
        if report["err"]:
            print("失败页：", report["err"])
        assert not report["err"], "有页面切不过去"
        print(f"全部页面切换完成 {rss():.1f} MB，共 {len(window._pages)} 页已建")
        window._shutdown()
        app.quit()

    QTimer.singleShot(1200, step)
    app.exec()
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
