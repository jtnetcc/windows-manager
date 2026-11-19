"""一次跑完所有自检脚本。

12 个 `tools/check_*.py` 各有各的入口，发版前逐个点太容易漏。这里统一跑一遍，
按脚本名排序输出 PASS/FAIL，最后给汇总；任一失败则退出码非 0，可以直接挂到 CI。

约定：
- 每个脚本独立进程运行，互不污染（有的会建 QApplication、有的会装一次程序）；
- 默认单个脚本 300 秒超时，超时算失败；
- `--headless` 跳过需要真实桌面/会改系统的脚本，给 CI 用；
- `--list` 只列脚本，不执行。

用法：
    python tools/check_all.py
    python tools/check_all.py --headless
    python tools/check_all.py --only check_dup_links check_sign
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"

# GitHub runner 的默认控制台是 cp1252，直接 print 中文会 UnicodeEncodeError
# （2026-11-19 首次跑 CI 就栽在这行上）。父进程和子进程都强制 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# CI / 无桌面环境不该跑的：真装卸、真关进程、依赖托盘与通知
HEADLESS_SKIP = {
    "check_uninstall_silent.py",   # 会静默安装再卸载一次
    "check_close.py",              # 依赖关闭对话框与托盘
    "check_tray.py",               # 托盘
}


def discover() -> list[Path]:
    return sorted(p for p in TOOLS.glob("check_*.py") if p.name != "check_all.py")


def run_one(script: Path, timeout: float) -> tuple[bool, str, float]:
    started = time.perf_counter()
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    # 子脚本同样在 cp1252 控制台下跑，不指定编码会在自己的 print 上崩
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        done = subprocess.run(
            [sys.executable, "-u", str(script)],
            cwd=str(ROOT),
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, f"超时（>{timeout:.0f}s）", time.perf_counter() - started

    cost = time.perf_counter() - started
    output = (done.stdout or b"").decode("utf-8", "replace")
    errors = (done.stderr or b"").decode("utf-8", "replace")
    if done.returncode == 0:
        tail = [line for line in output.splitlines() if line.strip()]
        return True, tail[-1] if tail else "OK", cost
    detail = [line for line in (errors or output).splitlines() if line.strip()]
    return False, detail[-1] if detail else f"退出码 {done.returncode}", cost


def main() -> int:
    parser = argparse.ArgumentParser(description="跑全部自检脚本")
    parser.add_argument("--only", nargs="*", default=[], help="只跑这些脚本（可省略 .py）")
    parser.add_argument("--headless", action="store_true", help="跳过需要真实桌面的脚本")
    parser.add_argument("--timeout", type=float, default=300, help="单个脚本超时（秒）")
    parser.add_argument("--list", action="store_true", help="只列脚本")
    args = parser.parse_args()

    scripts = discover()
    if args.only:
        wanted = {name if name.endswith(".py") else f"{name}.py" for name in args.only}
        scripts = [p for p in scripts if p.name in wanted]
        missing = wanted - {p.name for p in scripts}
        if missing:
            print(f"没找到：{', '.join(sorted(missing))}")
            return 2
    if args.headless:
        scripts = [p for p in scripts if p.name not in HEADLESS_SKIP]

    if args.list:
        for path in scripts:
            print(path.name)
        return 0

    print(f"共 {len(scripts)} 个自检脚本\n")
    failed: list[tuple[str, str]] = []
    total = 0.0
    for path in scripts:
        ok, note, cost = run_one(path, args.timeout)
        total += cost
        mark = "PASS" if ok else "FAIL"
        print(f"  {mark}  {path.name:<26} {cost:6.1f}s  {note[:80]}")
        if not ok:
            failed.append((path.name, note))

    print(f"\n合计 {total:.1f}s · 通过 {len(scripts) - len(failed)} / {len(scripts)}")
    if failed:
        print("失败项：")
        for name, note in failed:
            print(f"  - {name}: {note}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
