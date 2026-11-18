"""软件列表缓存的回归：第二次调用必须走缓存，refresh=True 必须绕过它。

一次 `list_installed()` 实测 2.1 秒（115 个软件，扫注册表 + 安装目录），
而软件管理页每次进入都会调一次，切页/换肤重建更是反复调 —— 缓存是必要的，
但必须满足三条：命中要快、不能把缓存的 list 交出去被调用方改坏、强制刷新要真横扫。

用法：python tools/check_apps_cache.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import apps  # noqa: E402


def main() -> int:
    apps.invalidate_cache()

    started = time.perf_counter()
    first = apps.list_installed(refresh=True)
    cold = time.perf_counter() - started
    assert first, "没扫到任何软件，环境不对"

    started = time.perf_counter()
    second = apps.list_installed()
    warm = time.perf_counter() - started

    assert [a.name for a in first] == [a.name for a in second], "缓存内容与首次扫描不一致"
    assert warm < 0.2, f"缓存没生效，第二次仍耗时 {warm:.2f}s"
    assert warm < cold / 5, f"缓存命中不够快：冷 {cold:.2f}s / 暖 {warm:.2f}s"
    print(f"缓存命中 ✓（冷 {cold:.2f}s → 暖 {warm*1000:.0f}ms，{len(first)} 个软件）")

    # 返回值必须是浅拷贝：调用方排序 / pop 不能污染缓存
    before = len(apps.list_installed())
    second.clear()
    after = len(apps.list_installed())
    assert after == before, "缓存里的 list 被调用方改坏了"
    print(f"返回值隔离 ✓（调用方 clear() 后仍有 {after} 个）")

    # 强制刷新要真的重扫
    started = time.perf_counter()
    apps.list_installed(refresh=True)
    forced = time.perf_counter() - started
    assert forced > warm * 5, f"refresh=True 没绕过缓存：{forced:.2f}s"
    print(f"强制刷新 ✓（{forced:.2f}s，未走缓存）")

    # 卸载动作后缓存作废
    apps.invalidate_cache()
    assert not apps._installed_cache, "invalidate_cache() 没清空"
    print("缓存失效 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
