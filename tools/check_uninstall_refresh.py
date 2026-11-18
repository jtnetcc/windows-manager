"""卸载后自动刷新回归：外部卸载向导退出后，列表必须自己更新。

以前 `_uninstall()` 只是 Popen 启动向导就结束，store 里的软件列表不变，
用户关掉向导回来仍看到那个软件 —— 只能手动点「重新扫描」。
这里用假进程模拟「向导已退出」，断言 store 与表格都自动清掉该项。

用法：python tools/check_uninstall_refresh.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from core import apps as appmgr  # noqa: E402
from core.store import SoftwareState, Store  # noqa: E402
from core.task import TaskCenter  # noqa: E402
from ui.page_software import InstalledView  # noqa: E402


class FakeProc:
    """模拟「卸载向导瞬间退出」。"""

    def wait(self, timeout=None):
        return 0


class SlowProc:
    """模拟「卸载向导一直不退出」。"""

    def wait(self, timeout=None):
        time.sleep(min(1.0, timeout or 1.0))
        raise TimeoutError("still running")


def main() -> int:
    app = QApplication(sys.argv[:1])
    store = Store()
    tasks = TaskCenter()
    # 主窗口本来会做这个连接；这里脱离主窗口单测，得自己接上
    tasks.finished.connect(store.apply)
    target = appmgr.InstalledApp(
        name="测试软件", uninstall=r"C:\Windows\System32\notepad.exe"
    )
    store.apply("software", SoftwareState(apps=[target], scanned_at=time.time()))

    view = InstalledView(store, tasks)
    view._render_software()
    assert view.table.rowCount() == 1, view.table.rowCount()

    threads: list = []
    # 打桩：确认框选「是」，启动器交回一个会立刻退出的假进程
    original_question = QMessageBox.question
    original_launch = appmgr.launch_uninstaller
    QMessageBox.question = staticmethod(lambda *_a, **_kw: QMessageBox.Yes)

    def fake_launch(item):
        item.uninstall_proc = FakeProc()
        return True, ""

    appmgr.launch_uninstaller = fake_launch
    try:
        view._uninstall(target)
        threads.append(view._uninstall_bg._thread)
    finally:
        QMessageBox.question = original_question
        appmgr.launch_uninstaller = original_launch

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        app.processEvents()
        names = {item.name for item in store.software.apps}
        if target.name not in names:
            break
        time.sleep(0.05)

    names = {item.name for item in store.software.apps}
    assert target.name not in names, f"store 里还留着已卸载软件：{names}"
    shown = {view.table.item(i, 0).text() for i in range(view.table.rowCount())}
    assert target.name not in shown, f"表格没有自动移除该行：{shown}"
    assert not view._uninstall_bg.busy, "后台等待线程没有结束"
    print(f"卸载后自动刷新 ✓（store 与 {view.table.rowCount()} 行表格均无该项，无需手动刷新）")

    # 超时分支：向导一直不退时只给提示，不能把界面卡住
    QMessageBox.question = staticmethod(lambda *_a, **_kw: QMessageBox.Yes)
    appmgr.launch_uninstaller = lambda item: (
        setattr(item, "uninstall_proc", SlowProc()) or (True, "")
    )
    try:
        view._uninstall(target)
        threads.append(view._uninstall_bg._thread)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            app.processEvents()
            if "仍在运行" in view.lbl_count.text():
                break
            time.sleep(0.05)
        assert "仍在运行" in view.lbl_count.text(), view.lbl_count.text()
        assert not view._uninstall_bg.busy
        print("超时分支 ✓（给出提示且后台等待已结束）")
    finally:
        QMessageBox.question = original_question
        appmgr.launch_uninstaller = original_launch

    # 等后台线程真的走完再退出。cancel() 只是丢结果、不会中断线程，
    # 在它还在写 _done 时让解释器收尾，Qt 偶发 abort（这里出现过 2/5）。
    # cancel() 会把 _thread 置空，所以必须拿住启动时的线程对象再 join，
    # 靠 busy / _thread is None 判断都会漏掉刚结束还在写字段的那一瞬
    for thread in threads:
        if thread is not None:
            thread.join(timeout=8)
    tasks.shutdown(wait_ms=5000)
    view.shutdown()
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    # 到这里所有自建线程都已 join、任务中心已 shutdown。
    # 让解释器自己去析构 PySide 对象会偶发 abort（Qt 插件收尾顺序），
    # 自检脚本不需要跑析构，直接退进程最稳。
    os._exit(code)
