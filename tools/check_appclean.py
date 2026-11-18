"""软件专清核心逻辑自检：发现 → 统计 → 删除 → 白名单边界。

用临时目录伪造一个软件结构，重点验证「不在白名单里的目录一个都不能少」：
数据库目录放在 `Image` 旁边，清完之后必须原封不动。

用法：python tools/check_appclean.py
"""

from __future__ import annotations

import os
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
            # discover() 还会自动识别本机真实存在的软件（比如 OneDrive），
            # 断言「只有一条」会随机器状态飘；只认注入的这条测试数据
            apps = [a for a in appclean.discover() if a.key == "test"]
            assert len(apps) == 1, apps
            app = apps[0]
            names = [t.name for t in app.targets]
            print("发现目录:", names)
            assert names == ["图片", "视频"], names

            stats = [s for s in appclean.scan() if s.app.key == "test"]
            assert stats, "没扫到注入的测试软件"
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

        test_auto_discover(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("OK")


def test_auto_discover(tmp: Path) -> None:
    """自适应识别：只从数据目录看出装了哪些软件，不看注册表清单。

    要证明六件事：
    1. 四层深的 `User Data\\Default\\Cache` 能找到（只扫一层根本看不见）
    2. `Local Storage`、`Session Storage` 这类装着用户数据的名字永远不进清单
    3. 厂商壳要拆成产品名：`Tencent\\WeChat` 显示「微信」，而不是「Tencent」
    4. 更新器、系统目录不冒充软件
    5. 家目录下的 `.workbuddy` 这类点目录要认出来（AppData 里根本没有它），
       但同级的 Desktop / Documents 一律不碰
    6. `plugins\\cache` 里装的是插件本体，不能因为名字叫 cache 就清掉
    """
    print("\n[自适应识别]")
    local = tmp / "local"
    roam = tmp / "roam"
    home = tmp / "home"

    write(local / "Foo Bar" / "User Data" / "Default" / "Cache" / "x.bin", 4096)
    write(local / "Foo Bar" / "User Data" / "Default" / "GPUCache" / "y.bin", 1024)
    # 这两个必须排除：名字像缓存，装的是用户数据
    write(local / "Foo Bar" / "User Data" / "Local Storage" / "k.db", 2048)
    write(roam / "Bar Baz" / "Session Storage" / "s.bin", 512)
    # 厂商壳 → 产品
    write(local / "Tencent" / "WeChat" / "Data" / "Cache" / "w.bin", 2048)
    write(local / "Tencent" / "QQ" / "Data" / "Cache" / "q.bin", 1024)
    # 更新器与系统目录，都不该冒充软件
    write(local / "FooBar-updater" / "Cache" / "u.bin", 1024)
    write(local / "Temp" / "Cache" / "t.bin", 1024)
    # 日志单独一类
    write(local / "Spotty" / "Logs" / "s.log", 2048)
    # 家目录点目录：WorkBuddy 这种只把数据放 `~/.workbuddy` 的程序
    write(home / ".workbuddy" / "cache" / "c.bin", 4096)
    write(home / ".workbuddy" / "logs" / "l.log", 2048)
    # 插件本体：目录名叫 cache，删了插件就没了
    write(home / ".workbuddy" / "plugins" / "cache" / "pkg" / "index.js", 8192)
    # 家目录里的普通目录不许扫，哪怕是名字叫 Cache 的
    write(home / "Desktop" / "Cache" / "d.bin", 4096)

    originals = {}
    for key, value in (
        ("LOCALAPPDATA", local),
        ("APPDATA", roam),
        ("USERPROFILE", home),
    ):
        originals[key] = os.environ.get(key)
        os.environ[key] = str(value)
    try:
        apps, info = appclean._discover_data_dirs([], set())
    finally:
        for key, value in originals.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    names = sorted(app.name for app in apps)
    print("识别到:", names, "| 跳过:", info.skipped)
    assert names == ["Foo Bar", "QQ", "Spotty", "WorkBuddy", "微信"], names

    found = {app.name: app for app in apps}
    caches = [p for t in found["Foo Bar"].targets for p in t.paths]
    assert str(local / "Foo Bar" / "User Data" / "Default" / "Cache") in caches, caches
    assert str(local / "Foo Bar" / "User Data" / "Default" / "GPUCache") in caches, caches
    assert not any("Local Storage" in p for p in caches), caches
    assert not any("Session Storage" in p for p in caches), caches

    kinds = {t.name: t.kind for t in found["Spotty"].targets}
    assert kinds.get("日志与转储") == "logs", kinds
    print("四层缓存命中 · User Data 下钻 · 厂商拆产品 · 更新器不冒充 ✓")

    home_caches = [p for t in found["WorkBuddy"].targets for p in t.paths]
    assert str(home / ".workbuddy" / "cache") in home_caches, home_caches
    assert str(home / ".workbuddy" / "logs") in home_caches, home_caches
    assert not any("plugins" in p for p in home_caches), home_caches
    assert not any("Desktop" in p for p in home_caches), home_caches
    print("家目录点目录认出 WorkBuddy · 插件本体不清 · 桌面不碰 ✓")


if __name__ == "__main__":
    main()
