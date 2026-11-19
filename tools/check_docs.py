"""文档一致性自检：版本号、脚本数量、历史文档标记、已删文件引用。

用法：python tools/check_docs.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.version import VERSION  # noqa: E402

README = ROOT / "README.md"
HANDOVER = ROOT / "docs" / "交接文档.md"
PACKAGING = ROOT / "docs" / "打包说明.md"
HISTORICAL = (
    "整改方案.md",
    "C盘扫描加强方案.md",
    "待接入排查与下步方案.md",
    "GrClean调研与整合方案.md",
)
DELETED = ("build_macos.py", "make_icns.py", "macOS适配指南.md", "icon.icns")


def main() -> int:
    readme = README.read_text(encoding="utf-8")
    handover = HANDOVER.read_text(encoding="utf-8")
    packaging = PACKAGING.read_text(encoding="utf-8")

    # 1. 版本号必须和唯一来源一致
    assert f"v{VERSION} · 内测" in readme, f"README 版本号不是 {VERSION}"
    assert f"v{VERSION}" in HANDOVER.read_text(encoding="utf-8")
    assert f"v{VERSION}" in packaging
    print(f"版本号一致 ✓（v{VERSION}）")

    # 2. 自检脚本数量（含本脚本）
    count = len(list((ROOT / "tools").glob("check_*.py")))
    assert f"{count} 个自检脚本" in readme, f"README 应写 {count} 个自检脚本"
    assert f"check_*.py            {count} 个自检脚本" in handover
    assert f"{count} 个自检里挑" in packaging
    print(f"脚本计数一致 ✓（{count} 个）")

    # 3. 历史文档必须自带现状标记，防止后来人照着过期方案干活
    for name in HISTORICAL:
        text = (ROOT / "docs" / name).read_text(encoding="utf-8")
        assert "现状校准" in text or "2026-11-19" in text, f"{name} 缺现状标记"
    print("历史文档标记 ✓（4 份）")

    # 4. 已删除文件只允许出现在「删除 / 历史」语境里
    allowed = ("删除", "已删", "停止", "历史", "旧", "不再")
    for path in [README, HANDOVER, PACKAGING] + [ROOT / "docs" / n for n in HISTORICAL]:
        for line in path.read_text(encoding="utf-8").splitlines():
            for name in DELETED:
                if name in line and not any(word in line for word in allowed):
                    raise AssertionError(f"{path.name} 仍在正面引用已删文件：{line[:90]}")
    print("已删文件引用 ✓（仅在删除/历史语境）")

    # 5. README / 文档里引用的预览图必须存在
    import re as _re

    for doc in (README, HANDOVER):
        for match in _re.finditer(r"\]\((docs/preview/[^)]+\.png)\)", doc.read_text(encoding="utf-8")):
            target = ROOT / match.group(1)
            assert target.exists(), f"{doc.name} 引用了不存在的截图：{match.group(1)}"
    print("预览图引用 ✓")

    # 6. 推送方式仍是 SOCKS5
    assert "socks5h://127.0.0.1:10808" in readme
    print("推送说明 ✓")
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
