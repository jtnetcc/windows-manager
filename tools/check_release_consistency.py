"""发版产物一致性：版本号、dist 与 releases 的便携版、安装包脚本。

v0.0.62 出过一次事故：版本号写的是 0.0.62，安装包里塞的却是 0.0.61 的 exe
（build.py 因 exe 被占用把新产物写进 dist_pending，而 dist 里旧文件还在，
打包脚本只看「dist 存在」就用了旧的）。这个脚本专门盯这类不一致。

用法：python tools/check_release_consistency.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.version import VERSION  # noqa: E402

NAME = "Windows系统管家"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    release = ROOT / "releases" / f"v{VERSION}"
    manifest = release / "manifest.json"
    if not manifest.exists() and not (ROOT / "dist" / f"{NAME}.exe").exists():
        # CI 只签出源码，没有本地发版产物；这里跳过而不是判失败
        print(f"跳过：没有 v{VERSION} 的发版产物（CI / 首次克隆属正常）")
        return 0
    assert manifest.exists(), f"缺少归档：{release}"
    meta = json.loads(manifest.read_text(encoding="utf-8"))
    meta_version = meta.get("version") or meta.get("ver") or ""
    assert VERSION in str(meta_version) or str(meta_version) == VERSION, (
        f"manifest 版本与 core/version.py 不一致：{meta_version} vs {VERSION}"
    )
    print(f"版本一致 ✓（v{VERSION}）")

    dist_exe = ROOT / "dist" / f"{NAME}.exe"
    rel_exe = release / f"{NAME}-v{VERSION}.exe"
    assert dist_exe.exists(), "dist 里没有便携版 exe"
    assert rel_exe.exists(), f"归档里没有便携版：{rel_exe.name}"
    same = sha(dist_exe) == sha(rel_exe)
    assert same, (
        "dist 便携版与归档不一致 —— 很可能 dist 里的 exe 被占用、新产物落到了 "
        "dist_pending（发 v0.0.62 时就是这个原因把旧 exe 塞进了安装包）"
    )
    print("便携版一致 ✓（dist == releases）")

    pending = ROOT / "dist_pending" / f"{NAME}.exe"
    if pending.exists() and pending.stat().st_mtime > dist_exe.stat().st_mtime:
        raise AssertionError(
            "dist_pending 里有比 dist 更新的 exe：dist 又是旧的，"
            "关掉运行中的程序后重新打包并覆盖"
        )
    print("dist_pending ✓（无更新产物残留）")

    setup = ROOT / "dist" / f"{NAME}-v{VERSION}-setup.exe"
    assert setup.exists(), "dist 里没有安装包"
    print(f"安装包存在 ✓（{setup.stat().st_size / 1048576:.1f} MB）")
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
