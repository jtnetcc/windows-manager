"""校验草稿 Release 的资产，通过后才允许公开。

输入是 `gh release view <tag> --json isDraft,assets` 落地的 JSON。
放在独立脚本里而不是 workflow 内联 heredoc：YAML 的块标量遇到顶格代码会截断，
这一课在 v0.0.63 的 CI 改造里已经踩过。

用法：python tools/check_release_assets.py release_check.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EXPECTED = 3  # 便携版 + 安装包 + SHA256SUMS


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "release_check.json")
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data.get("isDraft") is True, "发布前不是草稿状态，停止"
    assets = {a["name"]: a["size"] for a in data.get("assets", [])}
    assert len(assets) == EXPECTED, f"资产数量应为 {EXPECTED}，实际 {len(assets)}：{assets}"
    empty = [name for name, size in assets.items() if not size]
    assert not empty, f"存在 0 字节资产：{empty}"
    missing = [name for name in assets if not name.lower().endswith((".exe", ".txt"))]
    assert not missing, f"出现意外类型的资产：{missing}"
    print("资产自检通过：" + "，".join(f"{n} ({s:,} B)" for n, s in assets.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
