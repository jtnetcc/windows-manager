"""合并后的目视检查：软件管理 →「注册表残留」段的空状态与有数据状态。

跑法：python tools/preview_merge.py
产出：docs/preview/software-registry-{empty,items}.png
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core import config, registry
from core.store import RegistryState
from ui import theme_dark as T
from ui.main_window import MainWindow

OUT = ROOT / "docs" / "preview"


def fake_issues() -> list[registry.Issue]:
    """三条看着像真的的假数据，只为检查表格排版，不写任何注册表。"""
    return [
        registry.Issue(
            "uninstall",
            "HKLM",
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{9F1E2A3C-0000-4B7D-9A55-1C2D3E4F5A6B}",
            "",
            "旧版解压工具 3.2",
            "安装目录不存在",
            r"D:\Tools\OldUnzip",
        ),
        registry.Issue(
            "startup",
            "HKCU",
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
            "BaiduYunGuanjia",
            "BaiduYunGuanjia",
            "启动命令指向的文件不存在",
            r"C:\Program Files (x86)\Baidu\BaiduYunGuanjia\BaiduYunGuanjia.exe",
        ),
        registry.Issue(
            "com",
            "HKLM",
            r"SOFTWARE\Classes\CLSID\{3D1F0C24-8E7B-4C51-9A2E-77B6C0D9E5F1}\InprocServer32",
            "",
            "{3D1F0C24-8E7B-4C51-9A2E-77B6C0D9E5F1}",
            "组件所在目录不存在",
            r"C:\Program Files\OldShellExt\shellext.dll",
        ),
    ]


def main() -> int:
    app = QApplication(sys.argv[:1])
    settings = config.Settings.load()
    settings.theme = "light"
    T.set_accent(settings.accent or None)
    T.set_theme("light")
    app.setStyleSheet(T.app_stylesheet())
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow(settings)
    window.resize(1180, 780)
    window.show()

    # 老 key 仍旧能跳：应落到「软件管理 → 注册表残留」
    window.switch_page("registry")
    print("当前页:", window._current_page, "| 标题:", window.lbl_title.text())
    print("副标题:", window.lbl_sub.text())

    steps = []

    def grab(name: str) -> None:
        path = OUT / f"software-registry-{name}.png"
        window.grab().save(str(path))
        print("已保存", path.name)

    def finish() -> None:
        window._shutdown()
        app.quit()

    def step_empty() -> None:
        page = window._pages["software"].registry
        print("扫描状态:", page.lbl_stat.text(), "| 表可见:", page.table.isVisible(),
              "| 空态可见:", page.empty.isVisible())
        grab("empty")
        # 塞三条假数据，看有内容时的表格
        window.store.apply(
            "registry", RegistryState(issues=fake_issues(), scanned_at=time.time())
        )
        steps.append(QTimer.singleShot(600, step_items))

    def step_items() -> None:
        page = window._pages["software"].registry
        print("有数据:", page.lbl_stat.text(), "| 行数:", page.table.rowCount(),
              "| 表可见:", page.table.isVisible())
        grab("items")
        steps.append(QTimer.singleShot(200, finish))

    steps.append(QTimer.singleShot(3500, step_empty))
    app.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
