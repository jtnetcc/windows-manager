"""启动卡顿画像：跑真实的 MainWindow，量事件循环的停顿。

它回答三个问题：
1. 启动后界面有没有长时间不响应（定时器漂移 = UI 线程被占住）
2. 停顿发生在第几秒
3. 那一刻主线程卡在哪个函数上（faulthandler 定时 dump 全线程栈）

用法：

    python tools/profile_startup.py                 # 默认 90 秒
    python tools/profile_startup.py 180 0.2         # 秒数、阈值
    python tools/profile_startup.py 90 0.2 --show   # 真窗口（不 headless）

输出：
    终端：每次超过阈值的停顿 + 汇总
    build/profile_stacks.txt：卡顿期间的调用栈
"""

from __future__ import annotations

import faulthandler
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

duration = float(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else 90.0
threshold = float(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("-") else 0.3

if "--show" not in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core import applog, config  # noqa: E402
from ui import theme_dark  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

STACK_LOG = ROOT / "build" / "profile_stacks.txt"


def main() -> int:
    STACK_LOG.parent.mkdir(parents=True, exist_ok=True)
    applog.setup("profile")

    boot = time.perf_counter()
    app = QApplication(sys.argv[:1])
    settings = config.Settings.load()
    theme_dark.set_theme(settings.theme)
    app.setStyleSheet(theme_dark.app_stylesheet())
    window = MainWindow(settings)
    window.show()
    build_cost = time.perf_counter() - boot
    print(f"[profile] 窗口构建 {build_cost * 1000:.0f} ms")

    # 任何一次阻塞超过 2 秒，就把所有线程的栈写进文件（含主线程卡在哪一行）
    stack_file = open(STACK_LOG, "w", encoding="utf-8")
    dump_at = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0
    faulthandler.dump_traceback_later(dump_at, repeat=True, file=stack_file)

    start = time.perf_counter()
    last = start
    gaps: list[tuple[float, float]] = []

    def tick() -> None:
        nonlocal last
        now = time.perf_counter()
        gap = now - last
        last = now
        at = now - start
        if gap >= threshold:
            gaps.append((at, gap))
            print(f"[停顿] t={at:6.1f}s  卡了 {gap * 1000:7.0f} ms")
        if at >= duration:
            faulthandler.cancel_dump_traceback_later()
            timer.stop()
            app.exit(0)

    timer = QTimer()
    timer.setInterval(100)          # 每 100ms 一次：这次回调晚到多少就是 UI 卡了多久
    timer.timeout.connect(tick)
    timer.start()

    # 硬兜底：托盘图标会让 exec() 有时候收不回来，到了时间直接走人
    import threading

    def hard_exit() -> None:
        print(f"[profile] 强制退出（超出 {duration + 10:.0f}s）")
        sys.stdout.flush()
        os._exit(3)

    threading.Timer(duration + 10, hard_exit).start()

    app.exec()

    total_gaps = sum(g for _, g in gaps)
    print("\n[汇总]")
    print(f"  时长          {duration:.0f} s")
    print(f"  窗口构建      {build_cost * 1000:.0f} ms")
    print(f"  超过 {threshold * 1000:.0f}ms 的停顿  {len(gaps)} 次，累计 {total_gaps:.1f} s")
    if gaps:
        worst = sorted(gaps, key=lambda item: item[1], reverse=True)[:5]
        for at, gap in worst:
            print(f"    最严重：t={at:.1f}s  {gap * 1000:.0f} ms")
    print(f"  栈记录        {STACK_LOG}")
    stack_file.close()
    return 0


if __name__ == "__main__":
    code = main()
    # 采样线程是 QThread，进程可能不肯退出；强制收尾。
    # os._exit 不刷缓冲，所以先把输出冲出去，否则这里什么都看不到。
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
