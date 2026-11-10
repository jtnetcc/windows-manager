"""软件专清页截图。

默认塞一组假数据（微信 / QQ / Chrome），因为开发机上通常没装微信 QQ，
真扫出来只有两个浏览器，看不出多软件、多类目的排版。

用法：
    python tools/preview_appclean.py [out.png] [light|dark|real]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core import appclean, config
from ui import theme_dark
from ui.main_window import MainWindow

GB = 1024 ** 3
MB = 1024 ** 2


def _target(key: str, name: str, kind: str, note: str, order: int):
    return appclean.Target(key, name, kind, note, order, ["C:/stub"])


def fake_stats() -> list[appclean.AppStat]:
    wechat = appclean.App(
        "wechat",
        "微信",
        "聊天记录数据库不动，只清图片视频等缓存",
        [
            _target("wechat_image", "聊天图片", "image",
                    "聊天里看过的图片，删了会重新下载", 0),
            _target("wechat_video", "聊天视频", "video",
                    "聊天里看过的视频，删了会重新下载", 1),
            _target("wechat_file", "聊天文件", "file",
                    "聊天里收到的文件，重要文件请先另存", 2),
            _target("wechat_emoji", "表情包", "emoji",
                    "收藏的自定义表情，删了需要重新收藏", 3),
            _target("wechat_attach", "聊天附件", "image",
                    "图片视频的缩略图与附件缓存", 4),
            _target("wechat_cache", "程序缓存", "cache",
                    "网页缓存、小程序缓存，不影响登录", 5),
        ],
    )
    qq = appclean.App(
        "qq",
        "QQ",
        "只清接收的图片视频，聊天记录不动",
        [
            _target("qq_image", "聊天图片", "image", "聊天里看过的图片", 0),
            _target("qq_video", "聊天视频", "video", "聊天里看过的视频", 1),
            _target("qq_file", "接收文件", "file",
                    "好友发来的文件，重要文件请先另存", 2),
            _target("qq_audio", "语音消息", "file", "语音消息缓存", 3),
        ],
    )
    chrome = appclean.App(
        "chrome",
        "Chrome",
        "网页缓存与离线数据，不影响账号与书签",
        [
            _target("chrome_cache", "网页缓存", "cache",
                    "网页资源缓存，不影响登录状态", 0),
            _target("chrome_code", "脚本缓存", "cache", "网页脚本编译缓存，会自动重建", 1),
            _target("chrome_gpu", "显卡缓存", "cache", "GPU 着色器缓存，会自动重建", 2),
            _target("chrome_sw", "离线数据", "cache", "网页离线数据，不影响账号", 3),
        ],
    )
    edge = appclean.App(
        "edge",
        "Edge",
        "网页缓存与离线数据，不影响账号与收藏",
        [
            _target("edge_cache", "网页缓存", "cache",
                    "网页资源缓存，不影响登录状态", 0),
            _target("edge_code", "脚本缓存", "cache", "网页脚本编译缓存，会自动重建", 1),
        ],
    )

    return [
        appclean.AppStat(
            wechat,
            [
                appclean.TargetStat(wechat.targets[0], int(5.1 * GB), 18342),
                appclean.TargetStat(wechat.targets[1], int(2.4 * GB), 386),
                appclean.TargetStat(wechat.targets[2], int(0.8 * GB), 1294),
                appclean.TargetStat(wechat.targets[3], int(0.2 * GB), 2410),
                appclean.TargetStat(wechat.targets[4], int(90 * MB), 5120),
                appclean.TargetStat(wechat.targets[5], int(38 * MB), 910),
            ],
        ),
        appclean.AppStat(
            qq,
            [
                appclean.TargetStat(qq.targets[0], int(1.3 * GB), 5210),
                appclean.TargetStat(qq.targets[1], int(620 * MB), 84),
                appclean.TargetStat(qq.targets[2], int(410 * MB), 233),
                appclean.TargetStat(qq.targets[3], 0, 0),
            ],
        ),
        appclean.AppStat(
            chrome,
            [
                appclean.TargetStat(chrome.targets[0], int(240 * MB), 3480),
                appclean.TargetStat(chrome.targets[1], int(68 * MB), 1120),
                appclean.TargetStat(chrome.targets[2], int(12 * MB), 210),
                appclean.TargetStat(chrome.targets[3], int(26 * MB), 88),
            ],
        ),
        appclean.AppStat(
            edge,
            [
                appclean.TargetStat(edge.targets[0], int(31 * MB), 402),
                appclean.TargetStat(edge.targets[1], int(9 * MB), 96),
            ],
        ),
    ]


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "preview_appclean.png")
    mode = sys.argv[2] if len(sys.argv) > 2 else "dark"
    real = mode == "real"

    app = QApplication(sys.argv[:1])
    settings = config.Settings.load()
    theme_dark.set_accent(settings.accent or None)
    theme_dark.set_theme("light" if mode == "light" else "dark")
    app.setStyleSheet(theme_dark.app_stylesheet())
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow(settings)
    window.resize(1100, int(__import__("os").environ.get("PREVIEW_H", 720)))
    window.show()
    window._theme_watch.stop()
    window.switch_page("appclean")

    page = window.page_appclean

    def apply_fake() -> None:
        page._stats = fake_stats()
        page._phase = "ready"
        page._current = "wechat"
        page._checked = {"wechat_image", "chrome_cache"}
        page._rebuild_apps()
        page._render_detail()
        page._sync_buttons()
        page.lbl_state.setText(
            f"扫描完成：{len(page._stats)} 个软件 · 9.2 GB 可清理"
        )
        QTimer.singleShot(400, shot)

    def shot() -> None:
        window.grab().save(str(out))
        print("已保存", out, window.size().width(), window.size().height())
        window._shutdown()
        app.quit()

    # 真实模式等自动扫描出结果，假数据直接套
    QTimer.singleShot(3000 if real else 1200, shot if real else apply_fake)
    app.exec()


if __name__ == "__main__":
    main()
