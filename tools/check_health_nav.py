"""体检页「处理」按钮自检：不达标项出现按钮，点它能真的翻到对应页。

用法：python tools/check_health_nav.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core import cleaner, config, dup, health
from ui import theme_dark
from ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv[:1])
    settings = config.Settings.load()
    theme_dark.set_accent(settings.accent or None)
    theme_dark.set_theme("dark")
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
        for i in range(30)
    ]
    window.store.set_junk_items(items, sum(i.size for i in items), 0)
    window.store.set_dup_groups(
        [
            dup.DupGroup(
                size=int(2.1 * 1024 ** 3),
                files=["C:/a/setup.iso", "C:/b/setup.iso", "D:/bak/setup.iso"],
            )
        ]
    )
    # 必须真的停在这一页：不在前台时 isVisible() 恒为 False，按钮查不出来
    window.switch_page("health")

    def verify() -> None:
        page = window.page_health
        report = page._report
        assert report is not None, "体检没出结果"
        print(f"得分 {report.score} · {report.level}")
        print("不达标因子：")
        for factor, row in zip(report.factors, page.rows):
            if row.btn_go.isVisible():
                print(
                    f"  {factor.label}  {factor.score}/{factor.weight}"
                    f" → 页面 {health.FACTOR_PAGE.get(factor.key)!r}"
                )

        # 建议按钮
        advice_btns = [
            child
            for line in page._advice_rows
            for child in line.findChildren(type(page.btn_scan))
            if child.objectName() == "Link"
        ]
        print("建议行按钮数:", len(advice_btns))

        target = None
        for factor, row in zip(report.factors, page.rows):
            if row.btn_go.isVisible():
                target = health.FACTOR_PAGE.get(factor.key)
                row.btn_go.click()
                break
        assert target, "没有任何不达标项带处理按钮"
        assert window._current_page == target, (
            f"点「处理」没跳页：期望 {target}，实际 {window._current_page}"
        )
        print(f"点「处理」→ {window._current_page} ✓")

        # 再从建议行进一次（切回体检页再点）
        expected = next((item.page for item in report.advice if item.page), None)
        window.switch_page("health")
        if advice_btns and expected:
            advice_btns[0].click()
            print(f"点建议行「去处理」→ {window._current_page} ✓")
            assert window._current_page == expected, "建议行跳页不对"
        else:
            print("（本次没有带按钮的建议行，跳过）")

        window._shutdown()
        app.quit()

    QTimer.singleShot(4200, verify)
    app.exec()
    print("OK")


if __name__ == "__main__":
    main()
