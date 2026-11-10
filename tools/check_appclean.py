"""软件专清核心逻辑自检：发现 → 统计 → 删除 → 白名单边界。

用临时目录伪造一个软件结构，重点验证「不在白名单里的目录一个都不能少」：
数据库目录放在 `Image` 旁边，清完之后必须原封不动。

用法：python tools/check_appclean.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import appclean


def write(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="appclean_"))
    try:
        home = tmp / "Tencent Files"
        account = home / "10001"
        write(account / "Image" / "a.jpg", 1200)
        write(account / "Image" / "sub" / "b.jpg", 3400)
        write(account / "Video" / "v.mp4", 9000)
        # 下面这些不是白名单目录，删除后必须还在
        db = write(account / "Msg" / "msg.db", 7000)
        chat = write(account / "Msg3.0.db", 5000)

        # 用同一套 discover 逻辑，只是把 root 指到临时目录
        spec = appclean._Spec(
            "test",
            "测试软件",
            "测试用",
            (str(home),),
            (
                appclean._Kind("image", "图片", "image", "测试图片", "Image"),
                appclean._Kind("video", "视频", "video", "测试视频", "Video"),
            ),
        )
        original = appclean._specs
        appclean._specs = lambda: [spec]
        try:
            apps = appclean.discover()
            assert len(apps) == 1, apps
            app = apps[0]
            names = [t.name for t in app.targets]
            print("发现目录:", names)
            assert names == ["图片", "视频"], names

            stats = appclean.scan()
            stat = stats[0]
            print(f"统计: {stat.size} B · {stat.count} 个文件 · {stat.kinds} 类")
            assert stat.size == 1200 + 3400 + 9000, stat.size
            assert stat.count == 3, stat.count

            # 只清图片
            image = next(i.target for i in stat.items if i.target.name == "图片")
            result = appclean.purge([image])
            print(
                f"清理: 删 {result.deleted} · 失败 {result.failed} · "
                f"释放 {result.freed} B · 剩余 {result.remaining}"
            )
            assert result.deleted == 2 and result.freed == 4600, result
            assert not (account / "Image" / "a.jpg").exists()
            assert not (account / "Image" / "sub" / "b.jpg").exists()

            # 白名单边界：数据库和视频都必须还在
            assert db.exists(), "数据库被删了"
            assert chat.exists(), "聊天记录被删了"
            assert (account / "Video" / "v.mp4").exists(), "没选的视频被删了"
            print("白名单边界: 数据库 / 聊天记录 / 未选类目 均保留 ✓")

            # 清单里的路径要和「被删掉的文件」一致，垃圾清理页同步要用它
            assert str(account / "Image" / "a.jpg") in result.removed_paths
            assert str(db) not in result.removed_paths
            print("removed_paths 正确 ✓")
        finally:
            appclean._specs = original
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("OK")


if __name__ == "__main__":
    main()
