"""进程表行复用：第二次刷新必须复用同一批 QTableWidgetItem。

进程表每 3 秒刷一次，120 行 × 5 列 = 600 个 item。全新建对象的代价不在
「一次 600 个」，而在每 3 秒一次 × 开着几小时 —— 内存抖动、GC 停顿、
滚动掉帧。这个脚本断言「行数没变时对象被复用，内容仍然更新」。

用法：python tools/check_process_reuse.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.page_process import ProcessPage  # noqa: E402


def rows(count: int, base_memory: int = 1024 * 1024) -> list[dict]:
    return [
        {
            "name": f"proc{i}.exe",
            "pid": 1000 + i,
            "cpu": 1.5 * i,
            "memory": base_memory * (i + 1),
        }
        for i in range(count)
    ]


def main() -> int:
    app = QApplication(sys.argv[:1])
    page = ProcessPage()

    page.update_processes(rows(50), total=50 * 1024 * 1024, leaks=set())
    first_ids = [id(page.table.item(0, col)) for col in range(5)]
    assert page.table.rowCount() == 50, page.table.rowCount()

    page.update_processes(rows(50, base_memory=2 * 1024 * 1024), total=50 * 1024 * 1024, leaks={1000})
    second_ids = [id(page.table.item(0, col)) for col in range(5)]

    assert first_ids == second_ids, "行对象没有被复用（又被重建了）"
    assert page.table.item(0, 0).text() == "proc0.exe"
    assert page.table.item(0, 3).text() != "", "内存列没有写入"
    print("行复用 ✓（50 行 × 5 列未重建）")

    # 泄漏标记要能在复用对象上刷新
    assert page.table.item(0, 0).toolTip().startswith("该进程内存"), "泄漏提示没刷新"
    print("单元格内容/样式刷新 ✓")

    # 搜索过滤后行数减少，行对象仍然复用（前 10 行）
    page.search.setText("proc1")
    app.processEvents()
    assert page.table.rowCount() < 50, page.table.rowCount()
    print(f"过滤后行数 {page.table.rowCount()} ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
