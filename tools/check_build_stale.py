"""产物新鲜度守卫的回归：旧 exe 必须被拒绝打包。

v0.0.62 的事故链：build.py 因 exe 被占用把新产物写到 dist_pending，
而 dist 里的旧 exe 仍在；打包脚本只判断「dist 存在」→ 旧程序进了新安装包。
这里断言三层防线：取 mtime 更新的、旧产物直接拒绝、显式 --allow-stale 才放行。

用法：python tools/check_build_stale.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import build_installer as bi  # noqa: E402


def touch(path: Path, offset: float) -> None:
    """把 mtime 设成 现在+offset，避免依赖写文件顺序。"""
    now = time.time() + offset
    os.utime(path, (now, now))


def main() -> int:
    tmp = ROOT / "build" / "_stale_test"
    tmp.mkdir(parents=True, exist_ok=True)
    old_exe = tmp / "old.exe"
    new_exe = tmp / "new.exe"
    for path in (old_exe, new_exe):
        path.write_bytes(b"MZ")
    # 2 小时的偏移：源码 mtime 就在几分钟前，600 秒可能刚好卡在边界上
    touch(old_exe, -7200)
    touch(new_exe, -60)

    assert bi.choose_newest([old_exe, new_exe]) == new_exe, "没有选中较新的产物"
    assert bi.choose_newest([new_exe, old_exe]) == new_exe, "顺序影响结果"
    print("选包 ✓（dist/dist_pending 取 mtime 更新者）")

    # 旧产物必须被拒绝
    try:
        bi.assert_fresh(old_exe)
    except SystemExit as exc:
        assert "比源码旧" in str(exc), exc
    else:
        raise AssertionError("旧产物没有被拒绝")
    print("旧产物拒绝 ✓（SystemExit 且提示关掉运行中的程序）")

    # 新产物放行
    touch(new_exe, 600)
    bi.assert_fresh(new_exe)
    print("新产物放行 ✓")

    # 显式跳过只告警
    bi.assert_fresh(old_exe, allow_stale=True)
    print("--allow-stale ✓（告警不阻断）")

    # 真实仓库：当前 dist 便携版必须比源码新
    dist_exe = ROOT / "dist" / f"{bi.NAME}.exe"
    if dist_exe.exists():
        bi.assert_fresh(dist_exe)
        print("当前 dist 产物 ✓（不比源码旧）")

    for path in (old_exe, new_exe):
        path.unlink(missing_ok=True)
    tmp.rmdir()
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
