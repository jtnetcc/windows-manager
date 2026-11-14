"""内存口径自检：几个数字必须能对得上，不能互相矛盾。

跑法：python tools/check_mem.py

为什么单独做一个脚本：v0.0.14 犯过一个很隐蔽的错 ——
「使用中 12.83 GB + 可用 11.40 GB = 24 GB，物理内存只有 15.7 GB」，
用户拿截图来问「内存怎么这么高」。根因是 `used` 用页列表自己减
（total - free - standby - modified），而内核那次只回了 2.91 GB 的待命
（真实约 11.4 GB），缺口全算进了「使用中」，占用率从 28% 虚高到 81%。

这个脚本锁两件事：
1. 真机读数：`used + avail == total`，且四个细分相加也等于 total；
2. 构造「待命被少报」的那一帧，`_align()` 必须能把它修回来。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import winmem
from core.winmem import fmt_size as f, _align


def check_live() -> None:
    mem = winmem.read_memory()
    assert mem.used + mem.avail == mem.total, (
        f"使用中 + 可用 ≠ 物理内存：{f(mem.used)} + {f(mem.avail)} ≠ {f(mem.total)}"
    )
    total = mem.used + mem.standby + mem.modified + mem.free
    # 采集不是原子的（页列表和 GlobalMemoryStatusEx 两次调用之间内存会变），
    # 给 2% 的容差；真出 bug 时差额是几个 GB，远超这个数。
    assert abs(total - mem.total) <= mem.total * 0.02, (
        f"四项和与物理内存差太多：{f(total)} vs {f(mem.total)}"
    )
    print(
        f"真机 ✓  总 {f(mem.total)} · 使用 {f(mem.used)}（{mem.percent:.0f}%）"
        f" · 待命 {f(mem.standby)} · 空闲 {f(mem.free)} · 可用 {f(mem.avail)}"
    )


def check_partial_standby() -> None:
    """复现用户截图那一帧：待命只报了 2.91 GB，可用却有 11.40 GB。"""
    total = 15740 * winmem.MB
    avail = 11400 * winmem.MB
    free = 4 * winmem.MB
    standby = 2910 * winmem.MB
    modified = 0

    used = total - avail
    before = used + standby + modified + free
    assert before < total, "构造的数据本身就不该自洽，检查用例"

    nfree, nstandby, nmod = _align(total, avail, free, modified, standby)
    after = used + nstandby + nmod + nfree
    assert after == total, f"对齐后仍对不上：{f(after)} vs {f(total)}"
    assert nstandby > standby, "待命没有被补回来"
    print(
        f"少报待命 ✓  使用 {f(used)} + 待命 {f(nstandby)} = {f(after)}"
        f"（物理 {f(total)}）· 修复前只有 {f(before)}"
    )


def main() -> int:
    check_live()
    check_partial_standby()
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
