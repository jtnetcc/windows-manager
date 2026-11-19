"""归档保留策略的回归：只删最旧的、保护最近 N 个与已打 tag 的版本。

用临时目录造 15 个假版本，不碰真实 `releases/`（那里面是唯一的回滚源）。

用法：python tools/check_releases_prune.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import releases  # noqa: E402


def fake_versions(root: Path, count: int = 15) -> None:
    for minor in range(1, count + 1):
        folder = root / f"v0.0.{minor}"
        folder.mkdir(parents=True)
        (folder / "manifest.json").write_text('{"version": "0.0.%d"}' % minor, encoding="utf-8")


def main() -> int:
    tmp = ROOT / "build" / "_prune_test"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        fake_versions(tmp, 15)

        plan = releases.plan_prune(keep=10, root=tmp, protect=set())
        names = sorted(p.name for p in plan)
        assert names == [f"v0.0.{i}" for i in range(1, 6)], f"删除清单不对：{names}"
        print(f"保留策略 ✓（15 个 → 删最旧 5 个：{names}）")

        # 打 tag 的版本必须保护
        plan = releases.plan_prune(keep=3, root=tmp, protect={"v0.0.3"})
        assert (tmp / "v0.0.3") not in plan, "打了 tag 的版本被列入删除"
        assert len(plan) == 11, f"keep=3 时应删 11 个，实际 {len(plan)}"
        print("tag 保护 ✓（keep=3 时 v0.0.3 仍保留）")

        # 非版本文档（回滚备份）不参与
        (tmp / "_rollback_20260101").mkdir()
        plan = releases.plan_prune(keep=3, root=tmp, protect=set())
        assert all("_rollback_" not in p.name for p in plan)
        print("非版本目录 ✓（_rollback_* 不入清单）")

        # apply=False 不能真的删
        before = len(list(tmp.iterdir()))
        releases.prune(keep=10, apply=False)
        assert len(list(tmp.iterdir())) == before, "dry-run 删了文件"
        print("dry-run ✓（只列不删）")

        # 真实归档：只打印清单，确认不会误伤最近版本
        real = releases.plan_prune(keep=10)
        protected = {f"v{v}" for v in releases.tagged_versions()}
        assert all(p.name not in protected for p in real)
        newest = sorted((p for p in releases.RELEASES.iterdir() if p.is_dir()), key=releases._version_key)[-10:]
        assert all(p not in real for p in newest), "最近 10 个版本里有人被列入删除"
        print(f"真实归档 ✓（可删 {len(real)} 个，最近 10 个与 tag 均受保护）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
