"""v0.0.65 的四张代表性截图（概览 / 大文件 / 软件管理 / 悬停提示）。

必须在真实 Windows 平台跑（不要 offscreen）：离屏环境没有中文字体，
截图会整片渲染成方块。和 `preview_pages.py` 的区别：这里不跑真实扫描，直接把「已经扫完」的状态写进
store，再让页面按正常路径渲染 —— 截图要的是版式与信息密度，不是再验一遍扫描。

输出：docs/preview/preview_v065_*.png

    python tools/preview_v065.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core import config  # noqa: E402
from core.bigfile import BigFile
from core.store import BigFileState  # noqa: E402
from core.store import SoftwareState  # noqa: E402
from ui import theme_dark  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from core.apps import InstalledApp  # noqa: E402
from ui.tooltip import Tip  # noqa: E402

OUT = ROOT / "docs" / "preview"
SIZE = (1180, 760)
GB = 1024 ** 3

BIG = [
    ("VS2022_企业版.iso", 3.2, r"D:\下载\VS2022_企业版.iso", False),
    ("假期_vlog_青海湖.mp4", 2.4, r"D:\视频\假期_vlog_青海湖.mp4", False),
    ("Windows11_23H2.iso", 5.8, r"C:\Users\Jt\Downloads\Windows11_23H2.iso", False),
    ("演唱会现场直拍.mkv", 1.9, r"E:\媒体\演唱会现场直拍.mkv", False),
    ("系统备份_2025.wim", 12.4, r"D:\备份\系统备份_2025.wim", True),
    ("素材打包_2024.zip", 0.9, r"D:\工作\素材打包_2024.zip", False),
]

APPS = [
    ("Visual Studio Code", "1.95.2", "Microsoft", 412, "2026-03-11"),
    ("微信", "3.9.11", "腾讯", 1860, "2026-02-28"),
    ("Chrome", "131.0.6778", "Google", 720, "2026-03-02"),
    ("QQ", "9.9.15", "腾讯", 1420, "2026-01-17"),
    ("7-Zip", "24.09", "Igor Pavlov", 12, "2025-11-05"),
    ("Python 3.13", "3.13.1", "Python Software Foundation", 380, "2025-12-20"),
]


def wait(app: QApplication, ms: int) -> None:
    loop_end = QTimer()
    loop_end.setSingleShot(True)
    loop_end.start(ms)
    while loop_end.isActive():
        app.processEvents()


def settle(window: MainWindow, app: QApplication) -> None:
    """切页会触发自动扫描，截图前把队列清掉，免得「排队中/扫描中」进画面。"""
    window.tasks.cancel_all()
    wait(app, 300)


def grab(window: MainWindow, name: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"preview_v065_{name}.png"
    window.grab().save(str(path))
    print(f"已保存 {path.relative_to(ROOT)}")
    return path


def main() -> int:
    app = QApplication(sys.argv[:1])
    settings = config.Settings.load()
    theme_dark.set_theme(settings.theme)
    app.setStyleSheet(theme_dark.app_stylesheet())
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow(settings)
    window.resize(*SIZE)
    window.show()
    wait(app, 1200)

    # 1) 概览 / 体检
    window.switch_page("health")
    wait(app, 900)
    grab(window, "health")

    # 2) 空间分析 → 大文件（注入已扫完的状态，避免真扫盘）
    files = [
        BigFile(path, int(mb * GB), 1_760_000_000, locked=locked)
        for name, mb, path, locked in BIG
    ]
    window.switch_page("bigfile")
    settle(window, app)
    window.store.apply(
        "bigfile",
        BigFileState(
            files=files,
            total=sum(f.size for f in files),
            scanned_at=1_760_000_000,
            kinds=[],
            min_size=100 * 1024 ** 2,
        ),
    )
    page = window._pages["bigfile"]
    page.set_segment("files")
    wait(app, 900)
    grab(window, "space_files")

    # 3) 软件管理
    apps = [
        InstalledApp(name=name, version=ver, publisher=pub, size_kb=kb * 1024,
                     install_date=date, uninstall=r"C:\Program Files\uninstall.exe")
        for name, ver, pub, kb, date in APPS
    ]
    window.switch_page("software")
    settle(window, app)
    window.store.apply("software", SoftwareState(apps=apps, scanned_at=1_760_000_000))
    wait(app, 900)
    grab(window, "software")

    # 4) 悬停提示：把提示框画到窗口截图上（离屏环境拿不到真实合成）
    pix = window.grab()
    tip = Tip()
    tip.show_text(
        "列表合并两个来源：注册表登记的安装包 + 安装目录里带卸载程序的软件。\n"
        "卸载一律打开程序自带的向导，本工具不会替你点「下一步」。",
        QPoint(460, 300),
    )
    wait(app, 120)
    painter = QPainter(pix)
    tip.render(painter, QPoint(430, 322))
    painter.end()
    path = OUT / "preview_v065_tooltip.png"
    pix.save(str(path))
    print(f"已保存 {path.relative_to(ROOT)}")

    window._shutdown()
    app.quit()
    print("完成")
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
