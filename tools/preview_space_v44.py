"""空间分析页改版后的实机截图（扫描中 / 完成）。

不套整个 MainWindow：主窗口启动时会做单实例、主题监听、各页懒加载，
截图脚本卡在任何一个模态框上都只能干等。这里只把 SpaceAnalysisView 摆进
一个空白窗口，跑真扫描、真任务中心，出来的排版和主窗口里一致。

    python -u tools/preview_space_v44.py
"""

from __future__ import annotations

import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QObject, Qt, QTimer, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

from core.task import TaskCenter  # noqa: E402
from ui import theme_dark as T  # noqa: E402
from ui.page_space import SpaceAnalysisView  # noqa: E402

OUT = ROOT / "docs" / "preview"
LIMIT = 180.0


class StubStore(QObject):
    """只提供 SpaceAnalysisView 用到的两样东西。"""

    space_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.space = types.SimpleNamespace(ready=False, result=None)


def main() -> None:
    app = QApplication(sys.argv[:1])
    T.set_theme("light")
    app.setStyleSheet(T.app_stylesheet())

    tasks = TaskCenter()
    store = StubStore()

    host = QWidget()
    host.setStyleSheet(f"background: {T.BG};")
    layout = QVBoxLayout(host)
    layout.setContentsMargins(20, 16, 20, 16)
    view = SpaceAnalysisView(store, tasks)
    layout.addWidget(view)
    host.resize(1060, 720)
    host.show()

    started = time.monotonic()
    state = {"mid": False, "done": False}

    def tick() -> None:
        elapsed = time.monotonic() - started
        if not state["mid"] and view._busy and elapsed > 5:
            state["mid"] = True
            host.grab().save(str(OUT / "space-v44-scan.png"))
            print(f"[{elapsed:.1f}s] 扫描中截图 · 堆叠条段数 {len(view.bar._parts)}"
                  f" · 可回收行 {sum(1 for r in view.rec_rows if r.item)}")
            print("   结论句:", view.lbl_verdict.text())

        finished = view._result is not None and not view._busy
        if finished and not state["done"]:
            state["done"] = True
            print(f"[{elapsed:.1f}s] 扫描完成 {view._result.seconds:.0f}s"
                  f" · 已用 {view._result.used / 1024 ** 3:.1f} GB")
            print("   结论句:", view.lbl_verdict.text())
            for row in view.rec_rows:
                if row.item is not None:
                    print(f"   {row.item.name:10s} {row.item.size / 1024 ** 3:6.2f} GB"
                          f" · 按钮「{row.btn.text()}」· 命令 {bool(row.item.cmd)}")
            QTimer.singleShot(700, finish)
            return

        if elapsed > LIMIT:
            print("超时退出")
            finish()
            return
        QTimer.singleShot(300, tick)

    def finish() -> None:
        if view._result is not None:
            host.grab().save(str(OUT / "space-v44-done.png"))
            print("已保存 space-v44-done.png")
            demo_actions()
            return
        tasks.cancel_many("space", "space_sub", "space_refresh")
        app.quit()

    def demo_actions() -> None:
        """补一张「有禁用 / 清理按钮」的样子。

        本机已经把休眠关了，真扫描出不来这两项，所以这里塞两条合成数据 ——
        只看排版和按钮文案，数字不当真。
        """
        from core import spacereport

        extra = [
            spacereport.Reclaim(
                "hibernation", "休眠文件", 8 * 1024 ** 3, "和内存等量。",
                action="disable", cmd=("powercfg.exe", "/hibernate", "off"),
                verb="禁用", warn="关闭后无法休眠",
            ),
            spacereport.Reclaim(
                "restore_points", "系统还原点", 3.2 * 1024 ** 3, "历史快照。",
                action="open", cmd=("vssadmin.exe", "delete", "shadows"),
                verb="清理", warn="删除后无法回退",
            ),
        ]
        view._result.items = extra + view._result.items
        view._fill_reclaim()
        view._fill_verdict()
        QTimer.singleShot(500, shot_actions)

    def shot_actions() -> None:
        host.grab().save(str(OUT / "space-v44-actions.png"))
        print("已保存 space-v44-actions.png（禁用 / 清理按钮，合成两项示意）")
        tasks.cancel_many("space", "space_sub", "space_refresh")
        app.quit()

    QTimer.singleShot(200, view.start_scan)
    QTimer.singleShot(2200, tick)
    app.exec()


if __name__ == "__main__":
    main()
