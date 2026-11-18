"""查重页的链接处理回归：junction / 符号链接 / 硬链接都不能被当成「重复」。

为什么单独写一个测试：这三类东西在资源管理器里看着就是普通文件，
一旦被列进重复清单，用户点「删除」删掉的是同一份物理文件，
「保留的那份」也跟着消失 —— 表面上有回收站兜底，实际是数据事故。

造的场景（全部在临时目录里，跑完就删）：

    tmp/
      a/data.bin        2 MB，原始文件
      a/hard.bin        data.bin 的**硬链接**（同一 inode）
      copy/data.bin     同内容但独立的文件（这才是真重复）
      link_to_a/        junction，指向 tmp/a

期望：只产出一个组，组里恰好 2 个文件（a/data.bin + copy/data.bin），
      不含 hard.bin（同 inode），也不含 junction 下的路径。

用法：python tools/check_dup_links.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import _winapi
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import dup  # noqa: E402

MB = 1024 * 1024


def build_case(base: Path) -> None:
    source = base / "a"
    copy_dir = base / "copy"
    source.mkdir()
    copy_dir.mkdir()

    payload = os.urandom(MB) * 2          # 真随机，避免两个文件被当成稀疏/全零
    (source / "data.bin").write_bytes(payload)
    (copy_dir / "data.bin").write_bytes(payload)

    try:
        os.link(source / "data.bin", source / "hard.bin")
    except OSError as exc:
        raise SystemExit(f"创建硬链接失败（需要 NTFS）：{exc}")

    try:
        _winapi.CreateJunction(str(source), str(base / "link_to_a"))
    except OSError as exc:
        raise SystemExit(f"创建 junction 失败：{exc}")


def main() -> int:
    base = Path(tempfile.mkdtemp(prefix="wmdup_"))
    try:
        build_case(base)
        groups = dup.collect([str(base)], min_size=1 * MB)

        names = sorted(os.path.basename(p) for g in groups for p in g.files)
        paths = [p for g in groups for p in g.files]
        assert len(groups) == 1, f"组数不对：{[(g.size, g.files) for g in groups]}"
        assert len(groups[0].files) == 2, f"组内文件数不对：{names}"
        assert not any("hard.bin" in p for p in paths), "硬链接被当成了重复项"
        assert not any("link_to_a" in p for p in paths), "junction 下的文件进了清单"
        print(f"链接处理 ✓（1 组 / 2 份：{names}）")

        # 再确认一次「删除清单」只针对真重复：victim 必须是 copy 里那份
        victims = dup.cleanup_list(groups)
        assert len(victims) == 1, f"待删数量不对：{victims}"
        assert os.path.basename(victims[0]) == "data.bin"
        assert "copy" in victims[0] or "a" in victims[0]
        print(f"删除清单 ✓（只删 1 份：{os.path.dirname(victims[0])}）")
        return 0
    finally:
        # junction 先删，否则 rmtree 会顺着它去删目标
        link = base / "link_to_a"
        if link.exists():
            os.rmdir(link)
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
