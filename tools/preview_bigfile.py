"""大文件页截图：按类型扫描、排序、空态、深色主题。

造一批假文件（只占磁盘元数据，内容用稀疏写入），然后走真实的任务中心扫描，
保证截图里的数字、筛选条、排序都和实际运行一致。

    python tools/preview_bigfile.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core import config  # noqa: E402
from ui import theme_dark  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

FILES = {
    "假期_vlog_青海湖.mp4": 24,
    "演唱会现场直拍.mkv": 180,
    "动画合集_第一季.avi": 90,
    "会议录像_0312.mov": 66,
    "微信安装包_3.9.5.exe": 12,
    "VS2022_企业版.iso": 320,
    "素材打包_2024.zip": 45,
    "旅行照片备份.7z": 88,
    "README.txt": 2,
}


def make_files() -> str:
    folder = os.path.join(os.environ["TEMP"], "bigfile_preview")
    os.makedirs(folder, exist_ok=True)
    for name, mb in FILES.items():
        path = os.path.join(folder, name)
        if os.path.exists(path):
            continue
        # 稀疏文件：立刻有体积，不真占盘
        with open(path, "wb") as fh:
            fh.seek(mb * 1024 * 1024 - 1)
            fh.write(b"\0")
    return folder


def main() -> None:
    folder = make_files()
    app = QApplication(sys.argv[:1])
    settings = config.Settings.load()
    # 固定成「浅色」：否则 4 秒一次的系统主题轮询发现系统是深色，
    # 会把整个界面重建掉，截图过程中页面对象会突然失效
    settings.theme = "light"
    theme_dark.set_theme("light")
    app.setStyleSheet(theme_dark.app_stylesheet())
    window = MainWindow(settings)
    window.resize(1100, 720)
    window.show()
    window.switch_page("bigfile")
    # 页面合并后 window.page_bigfile 是 SpacePage 外壳，大文件视图在 .files
    space = window.page_bigfile
    # 空间分析会在建页时自动开扫，和截图用的扫描抢串行队列，先掐掉并切到「大文件」段
    window.tasks.cancel_many("space", "space_sub", "space_refresh")
    space.set_segment("files")
    page = space.files
    page._roots = [folder]
    page.root_box.blockSignals(True)
    page.root_box.setCurrentIndex(page._custom_scope_index())  # 自定义目录
    page.root_box.blockSignals(False)

    def snap(name: str) -> None:
        try:
            window._toast.hide()  # 内存提示会盖住底部说明
        except Exception:
            pass
        app.processEvents()
        window.grab().save(name)
        print("已保存", name)

    def video() -> None:
        page._on_type_selected("video")
        page.size_box.setCurrentIndex(0)
        page.start_scan()
        QTimer.singleShot(3000, video_done)

    def video_done() -> None:
        for row in range(min(2, page.table.rowCount())):
            page.table.item(row, 0).setCheckState(Qt.Checked)
        app.processEvents()
        snap("preview_bigfile_video.png")
        page._sort_by(1)          # 按名称排序，验证排序不丢勾选
        app.processEvents()
        snap("preview_bigfile_sort.png")
        page._sort_by(3)          # 回到按体积
        page._on_type_selected("installer")  # 安装包
        page.start_scan()
        QTimer.singleShot(3000, installer_done)

    def installer_done() -> None:
        app.processEvents()
        snap("preview_bigfile_installer.png")
        page._on_type_selected("image")  # 图片：演示目录里没有 -> 空态
        page.start_scan()
        QTimer.singleShot(2500, empty_done)

    def empty_done() -> None:
        app.processEvents()
        snap("preview_bigfile_empty.png")
        page._on_type_selected("all")
        page.size_box.setCurrentIndex(0)
        page.start_scan()
        QTimer.singleShot(3000, all_done)

    def all_done() -> None:
        app.processEvents()
        snap("preview_bigfile_all.png")
        window.resize(980, 640)
        QTimer.singleShot(400, small_done)

    def small_done() -> None:
        page._on_type_selected("video")
        page.size_box.setCurrentIndex(0)
        page.start_scan()
        QTimer.singleShot(3000, small_shot)

    def small_shot() -> None:
        snap("preview_bigfile_min.png")
        window._shutdown()
        app.quit()

    # 建页时用 singleShot(0) 排了一次空间分析扫描，会在我们取消之后才真正启动，
    # 再补一枪取消，否则后面的扫描全排在它后面（串行队列）
    QTimer.singleShot(600, lambda: window.tasks.cancel_many("space", "space_sub", "space_refresh"))
    QTimer.singleShot(700, video)
    app.exec()


if __name__ == "__main__":
    main()
