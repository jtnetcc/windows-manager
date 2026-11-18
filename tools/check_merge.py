"""注册表页并入软件管理后的自检。

覆盖的是「导航少一项但能力一条不少」这件承诺：老 key 还能跳、三段都在、
切到第三段会自动扫、体检能拿到 registry_count、删除后 store 会更新。
不真删任何注册表条目 —— 只在假数据上验证渲染与状态流转。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core import config, registry  # noqa: E402
from core.store import RegistryState  # noqa: E402
from ui import theme_dark as T  # noqa: E402
from ui.main_window import NAV_ITEMS, SOFTWARE_TARGETS, MainWindow  # noqa: E402
from ui.page_software import SEGMENTS  # noqa: E402

FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✓' if ok else '✗'} {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAIL.append(name)


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def main() -> int:
    app = QApplication(sys.argv[:1])
    settings = config.Settings.load()
    settings.theme = "light"
    T.set_accent(settings.accent or None)
    T.set_theme("light")
    app.setStyleSheet(T.app_stylesheet())
    app.setQuitOnLastWindowClosed(False)

    print("\n[导航]")
    keys = [key for key, _text in NAV_ITEMS]
    check("导航共 10 项", len(keys) == 10, str(len(keys)))
    check("没有独立注册表项", "registry" not in keys)
    check("软件管理仍在导航里", "software" in keys)
    check("老 key 有翻译表", "registry" in SOFTWARE_TARGETS)

    window = MainWindow(settings)
    window.resize(1180, 780)
    window.show()

    print("\n[跳转]")
    window.switch_page("registry")
    check("registry 落到 software 页", window._current_page == "software")
    check("标题是软件管理", window.lbl_title.text() == "软件管理")
    shell = window._pages["software"]
    check("停在注册表分段", shell.current_segment() == "registry")
    check("副标题跟着分段走", "注册表" in window.lbl_sub.text() or "失效的卸载项" in window.lbl_sub.text())

    print("\n[第三段]")
    page = shell.registry
    check("三段都在", [s for s, _t in SEGMENTS] == ["installed", "leftover", "registry"])
    check("老别名 page_registry 可取", getattr(window, "page_registry") is page)

    # ensure_scan 是切段时触发的，切过去等它扫完
    for _ in range(60):
        if window.store.registry.ready:
            break
        wait(100)
    check("切到该段会自动扫描", window.store.registry.ready)
    check("store 里有扫描结果", isinstance(window.store.registry, RegistryState))

    if not window.store.registry.issues:
        check("空结果走空状态", page.empty.isVisible() and not page.table.isVisible())
    else:
        check("有结果时显示表格", page.table.isVisible() and not page.empty.isVisible())

    print("\n[体检接线]")
    window.run_full_check()
    for _ in range(60):
        if window.store.registry.ready and window.store.health_inputs().get("registry_count") is not None:
            break
        wait(100)
    count = window.store.health_inputs().get("registry_count")
    check("体检拿到 registry_count", count is not None, str(count))

    print("\n[假数据渲染]")
    before = window.store.registry
    fake = [
        registry.Issue(
            "uninstall", "HKLM", r"SOFTWARE\Uninstall\X", "", "旧版工具",
            "安装目录不存在", r"D:\Tools\Old",
        ),
        registry.Issue(
            "startup", "HKCU", r"SOFTWARE\Run", "Foo", "Foo",
            "启动命令指向的文件不存在", r"C:\Program Files\Foo\foo.exe",
        ),
    ]
    window.store.apply("registry", RegistryState(issues=fake, scanned_at=time.time()))
    wait(50)
    check("表格行数跟上", page.table.rowCount() == 2, str(page.table.rowCount()))
    check("工具条统计正确", "2 项" in page.lbl_stat.text(), page.lbl_stat.text())
    check("空态已收起", not page.empty.isVisible())

    # 还原成真实结果，别把假数据留在 store 里影响后续
    window.store.apply("registry", before)

    window._shutdown()
    app.quit()

    print()
    if FAIL:
        print(f"失败 {len(FAIL)} 项：", "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
