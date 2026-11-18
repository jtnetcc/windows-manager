"""第三批结构改造的回归：测量只剩一份、子进程只剩一处。

两条断言：
1. `slim` 与 `spacereport` 算休眠文件体积时必须走**同一个函数**
   —— 打桩 `spacemeasure.hibernation_size`，两边都要看到这个假值；
2. `core/` 里除了 `syscmd` 与 `apps`（要用 shell/特殊 flag 起卸载器），
   不许再出现 `subprocess.run(`。

用法：python tools/check_measure_source.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import slim, spacereport, spacemeasure  # noqa: E402

CORE = ROOT / "core"
# 子进程必须统一走 core/syscmd.py；这两个是明确的例外：
#   syscmd  —— 封装本身
#   apps    —— 启动第三方卸载向导，要 shell 语法与 CREATE_NO_WINDOW 兜底
ALLOW_SUBPROCESS = {"syscmd.py", "apps.py"}


def check_measure_source() -> None:
    fake = 12345
    original = spacemeasure.hibernation_size
    spacemeasure.hibernation_size = lambda: fake
    try:
        assert slim._hiber_size() == fake, "slim 没有走 spacemeasure"
        item = spacereport._hibernation()
        assert item is not None and item.size == fake, "spacereport 没有走 spacemeasure"
    finally:
        spacemeasure.hibernation_size = original
    print("休眠文件体积：slim 与 spacereport 同源 ✓")


def check_syscmd_only() -> None:
    offenders = []
    for path in sorted(CORE.glob("*.py")):
        if path.name in ALLOW_SUBPROCESS:
            continue
        source = path.read_text(encoding="utf-8")
        if "subprocess.run(" in source:
            offenders.append(path.name)
    assert not offenders, f"这些模块还在自己起子进程：{offenders}"
    print(f"子进程封装统一 ✓（{len(list(CORE.glob('*.py')))} 个模块，仅 syscmd/apps 例外）")


def main() -> int:
    check_measure_source()
    check_syscmd_only()
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
