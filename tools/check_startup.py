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
import time
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
    labels = window._pages["about"].findChildren(QLabel)
    texts = [lb.text() for lb in labels]
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
        # 卡片里为了对齐会把长地址的中间省掉（见 page_about._short_address），
        # 所以这里校验「域名露出来了」+「tooltip 里有完整地址」两件事。
        host = channel.url.split("://", 1)[-1].split("/", 1)[0]
        assert any(host in t for t in texts), f"关于页没显示地址：{channel.name}"
        assert any(
            channel.url in lb.toolTip() for lb in labels
        ), f"关于页的 tooltip 里没有完整地址：{channel.name}"
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
            print(f"全部 {len(keys)} 项导航都能切，已建 {len(window._pages)} 页")
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
        # 磁盘空间：两个分段都要能切，且大文件段默认扫整个系统盘 ——
        # 单分区机器上「用户目录」只覆盖 C://Users//xxx，等于什么都没扫到
        from core import bigfile as bf

        win.switch_page("bigfile")
        space_page = win._pages["bigfile"]
        for key in ("analysis", "files"):
            space_page.set_segment(key)
            app.processEvents()
            assert space_page.current_segment() == key, f"分段切不过去：{key}"
        assert space_page.files._roots == bf.preset_roots("system"), "大文件默认范围不是系统盘"
        assert bf.PRESETS[0][0] == "system", "「系统盘」不在范围列表第一位"
        print("磁盘空间两个分段可切 · 大文件默认系统盘 ✓")
        win.tasks.cancel_many("space", "space_sub")

        # 系统信息：激活卡要起 cscript，1~2 秒才回来，单独排一个轮询
        win.switch_page("sysinfo")
        wait_activation(win, time.monotonic() + 25)

    def wait_activation(win, deadline: float) -> None:
        page = win._pages.get("sysinfo")
        if page is not None and (page._activation_loading or page._storage_loading):
            if time.monotonic() < deadline:
                QTimer.singleShot(300, lambda: wait_activation(win, deadline))
                return
            raise AssertionError("激活或存储信息 25 秒没回来")
        activation_card(win)

    def activation_card(win) -> None:
        from core import activation as act

        page = win._pages["sysinfo"]
        rows = page.row_act
        expected = {"激活状态", "激活方式", "系统激活时间", "硬件激活时间",
                    "产品密钥", "产品 ID"}
        assert set(rows) == expected, f"激活卡的项不对：{set(rows) ^ expected}"
        info = act.collect()
        # 这台机器上一定有许可证库，时间推不出来说明路径或 ctime 读错了
        assert info.system_time, "系统激活时间没推出来"
        assert info.system_note, "系统激活时间缺来源说明"
        status = rows["激活状态"].lbl_value.text()
        assert status not in ("", "读取中…", "—"), f"激活状态没填：{status}"
        print(f"系统信息激活卡 ✓（{status} · {info.system_time} · "
              f"硬件 {info.hardware_time or '无记录'}）")

        # 存储设备：每块盘两行，卡里行数必须对得上
        assert page._disks, "存储设备没读出来"
        assert page.disk_box.count() >= len(page._disks) * 2, "存储设备卡行数不对"
        assert page.row_board["主板序列号"].lbl_value.text() != "读取中…"
        # 显卡已并进「显示与网络」，主机名与「计算机名」重复、已删
        assert "主机名" not in page.row_net, "重复的「主机名」又加回去了"
        hours = [d.hours_text for d in page._disks]
        print(f"存储设备 {len(page._disks)} 块 ✓（{page._disks[0].model} · "
              f"通电 {hours[0]}）")

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
        # 每核占用只该有一份：总览和 CPU 段都放同一张 CoreTiles，
        # 等于把「哪几个核在忙」看两遍，还挤掉了总览该有的内存构成
        from ui.components import CoreTiles

        assert not monitor._pages["overview"].findChildren(CoreTiles), (
            "总览又出现了每核占用"
        )
        assert monitor._pages["cpu"].findChildren(CoreTiles), "CPU 段缺每核占用"
        print("每核占用只在 CPU 段出现 ✓")
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
        print(f"换肤后重新建页完成 {rss():.1f} MB")
        window._shutdown()
        app.quit()

    QTimer.singleShot(1200, step)
    app.exec()
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
