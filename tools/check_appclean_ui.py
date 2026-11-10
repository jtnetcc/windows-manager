"""软件专清页面的交互自检：勾选 → 按钮状态 → 清理后回写。

不真的删文件：直接构造一个 PurgeResult 调 `_apply_purge`，验证统计清零、
勾选清空、按钮回到禁用态。

用法：python tools/check_appclean_ui.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QPushButton

from core import appclean, config
from preview_appclean import fake_stats
from ui import theme_dark
from ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv[:1])
    settings = config.Settings.load()
    theme_dark.set_theme("dark")
    app.setStyleSheet(theme_dark.app_stylesheet())
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow(settings)
    window.resize(1100, 720)
    window.show()
    window._theme_watch.stop()
    window.switch_page("appclean")
    page = window.page_appclean

    def verify() -> None:
        try:
            check()
        except Exception:
            import traceback

            traceback.print_exc()
            window._shutdown()
            app.exit(1)
            return
        window._shutdown()
        app.quit()

    def check() -> None:
        page._stats = fake_stats()
        page._phase = "ready"
        page._current = "wechat"
        page._checked = set()
        page._rebuild_apps()
        page._render_detail()

        print("软件行:", [r.app.name for r in page._app_rows])
        assert len(page._app_rows) == 4
        assert page.lbl_sum.text().startswith("合计"), page.lbl_sum.text()
        assert not page.btn_clean.isEnabled(), "没勾选时不该能点清理"

        # 勾选第一类（微信图片）
        page._rows[0].check.setChecked(True)
        print("勾选后:", page.lbl_pick.text(), "| 按钮:", page.btn_clean.text())
        assert page.btn_clean.isEnabled()
        assert "已选 1 类" in page.lbl_pick.text(), page.lbl_pick.text()

        # 切到 Chrome 再勾一个，验证跨软件累加
        page._select_app("chrome")
        assert page.lbl_app.text() == "Chrome", page.lbl_app.text()
        page._rows[0].check.setChecked(True)
        assert "已选 2 类" in page.lbl_pick.text(), page.lbl_pick.text()
        print("跨软件勾选:", page.lbl_pick.text())

        # 模拟清理结果
        page._purge_keys = set(page._checked)
        before = page.lbl_sum.text()
        result = appclean.PurgeResult(
            deleted=120, failed=0, freed=5 * 1024 ** 3, remaining=0,
            removed_paths={"C:/nonexistent"},
        )
        page._phase = "cleaning"
        page._apply_purge(result)
        print("清理后:", page.lbl_state.text())
        assert "已释放" in page.lbl_state.text()
        assert "已选 0 项" == page.lbl_pick.text(), page.lbl_pick.text()
        assert not page.btn_clean.isEnabled()

        wechat = next(s for s in page._stats if s.app.key == "wechat")
        assert wechat.items[0].count == 0, "微信图片没被清零"
        print(f"合计 {before} → {page.lbl_sum.text()}")
        assert page.lbl_sum.text() != before

        # 清空后的类目不给勾
        page._select_app("wechat")
        assert not page._rows[0].check.isEnabled(), "空类目应该禁止勾选"

    QTimer.singleShot(900, verify)
    app.exec()
    print("OK")


if __name__ == "__main__":
    main()
