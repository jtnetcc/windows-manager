"""冷启动分段计时：打包后跑它，看时间花在解包、导入还是网络。

用法（打包后）：

    wm_boot.exe            → 把分段耗时写到 %TEMP%\\wm_boot.txt

分段：
  t0  进程启动（打包器解包之前，用 bootloader 的 _MEIPASS 时间近似不了，
      所以第一段实际是「解释器起来 + 本文件开始执行」）
  t1  import PySide6.QtWidgets 之后
  t2  import ui.main_window 之后
  t3  updater.check() 之后（这一项会联网，单独计）
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

T0 = time.perf_counter()
lines = [f"start at {time.time():.3f}"]


def mark(text: str) -> None:
    lines.append(f"{text}: {time.perf_counter() - T0:.2f} s")


mark("解释器就绪")
import PySide6  # noqa: E402

mark("import PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

mark("import QtWidgets")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from ui.main_window import MainWindow  # noqa: E402,F401

    mark("import ui.main_window")
except Exception as exc:  # noqa: BLE001
    lines.append(f"import ui.main_window 失败: {exc}")

try:
    from core import updater  # noqa: E402

    mark("import updater")
    info = updater.check()
    mark(f"updater.check ok={info.ok}")
except Exception as exc:  # noqa: BLE001
    lines.append(f"updater.check 失败: {exc}")

target = os.path.join(tempfile.gettempdir(), "wm_boot.txt")
with open(target, "w", encoding="utf-8") as handle:
    handle.write("\n".join(lines))
