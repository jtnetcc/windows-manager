"""关闭窗口三种处理的回归。

- ask：弹对话框；取消 → 窗口留着、配置不动
- ask + 选「最小化到托盘」并勾记住 → 隐藏 + 写配置
- tray：直接隐藏，进程不退出
- 设置页下拉能改，并且和对话框「记住」的结果对上
- quit：真退出（事件循环停掉，托盘摘掉）

用法：python tools/check_close.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core import config
from ui import theme_dark
from ui.main_window import CloseChoiceDialog, MainWindow


def main() -> None:
    app = QApplication(sys.argv[:1])
    settings = config.Settings.load()
    theme_dark.set_theme("dark")
    app.setStyleSheet(theme_dark.app_stylesheet())
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow(settings)
    window._theme_watch.stop()
    window.show()
    # 别把测试结果写进用户的真配置
    settings.save = lambda: None  # type: ignore[method-assign]

    queue: list = []

    def advance() -> None:
        if queue:
            QTimer.singleShot(80, queue.pop(0))
        else:
            window._shutdown()
            app.quit()

    def find_dialog() -> CloseChoiceDialog:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, CloseChoiceDialog), f"没弹出关闭对话框：{dialog!r}"
        return dialog

    # -- 1. ask + 取消
    def case_cancel() -> None:
        settings.close_action = config.ASK
        QTimer.singleShot(30, window.close)
        QTimer.singleShot(350, lambda: find_dialog().reject())
        QTimer.singleShot(700, check_cancel)

    def check_cancel() -> None:
        assert window.isVisible(), "取消之后窗口不该消失"
        assert settings.close_action == config.ASK, "取消不该改配置"
        print("ask · 取消 → 窗口保留 ✓")
        advance()

    # -- 2. ask + 最小化到托盘 + 记住
    def case_remember() -> None:
        settings.close_action = config.ASK
        QTimer.singleShot(30, window.close)
        QTimer.singleShot(350, choose_tray)

    def choose_tray() -> None:
        dialog = find_dialog()
        dialog.chk_remember.setChecked(True)
        dialog.btn_tray.click()
        QTimer.singleShot(400, check_remember)

    def check_remember() -> None:
        assert not window.isVisible(), "选了托盘窗口还开着"
        assert settings.close_action == config.TRAY, settings.close_action
        print("ask · 最小化到托盘 + 记住 → 隐藏并落配置 ✓")
        window.show()
        advance()

    # -- 3. tray 直通
    def case_tray() -> None:
        settings.close_action = config.TRAY
        QTimer.singleShot(30, window.close)
        QTimer.singleShot(400, check_tray)

    def check_tray() -> None:
        assert not window.isVisible(), "tray 档位下关闭没隐藏"
        print("tray · 直接关闭 → 隐藏 ✓")
        window.show()
        advance()

    # -- 4. 设置页下拉
    def case_settings() -> None:
        window.switch_page("settings")
        QTimer.singleShot(700, check_settings)

    def check_settings() -> None:
        page = window._pages["settings"]
        assert page.combo_close.currentData() == config.TRAY, "下拉没跟上当前值"
        page.combo_close.setCurrentIndex(page.combo_close.findData(config.QUIT))
        assert settings.close_action == config.QUIT, "改下拉没写进配置"
        print("设置页下拉与对话框结果一致 ✓")
        advance()

    # -- 5. quit
    def case_quit() -> None:
        QTimer.singleShot(30, window.close)

    queue = [case_cancel, case_remember, case_tray, case_settings, case_quit]
    QTimer.singleShot(300, advance)
    app.exec()

    # 走到这里说明 quit 分支真的把事件循环停掉了
    assert not window.tray.isVisible(), "退出时托盘没摘掉"
    print("quit · 关闭 → 事件循环停止、托盘摘掉 ✓")
    print("关闭处理全部通过")


if __name__ == "__main__":
    main()
