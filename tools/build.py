"""一键打包成单个 exe。

运行：
    python tools/build.py          用当前版本号打包，并归档到 releases/vX.Y.Z/
    python tools/build.py --bump   先把 PATCH +1 再打包（发新版用这个）
    python tools/build.py --no-archive   只打包，不归档

产物：dist/Windows系统管家.exe
归档：releases/vX.Y.Z/（source.zip + 带版本号的 exe + manifest.json）
回滚：python tools/releases.py --to 0.0.1
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "Windows系统管家"

sys.path.insert(0, str(ROOT))
from core import version  # noqa: E402
from tools import releases  # noqa: E402

EXCLUDES = [
    "tkinter",
    "unittest",
    "pydoc",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtTest",
    "PySide6.QtDBus",
    "PySide6.QtDesigner",
    "PySide6.QtSql",
    "PySide6.QtXml",
]

# PyInstaller 单文件 exe 的归档尾标记（末尾那个 CArchive cookie 的 magic）。
# 文件被传输截断时，MZ 头可能还在，但这个标记会丢 —— 这是「文件或目录损坏且
# 无法读取」在打包侧唯一能提前发现的特征。
COOKIE = b"MEI\014\013\012\013\016"


def verify_exe(path: Path) -> list[str]:
    """打包产物自检，返回问题列表（空列表表示完好）。

    用户把 exe 发到另一台机器上双击，报「文件或目录损坏且无法读取」
    （0x80070570）时，99% 是文件没传完整，而不是程序写坏了。发出去之前先确认
    手上这份是完整的：MZ 头、PE 签名、PyInstaller 归档尾、体积。
    """
    problems: list[str] = []
    size = path.stat().st_size
    with path.open("rb") as handle:
        head = handle.read(1024)
        handle.seek(max(0, size - 8192))
        tail = handle.read()

    if head[:2] != b"MZ":
        problems.append("缺少 MZ 头，不是有效的 Windows 可执行文件")
    if b"PE\x00\x00" not in head:
        problems.append("找不到 PE 签名（前 1 KB）")
    if COOKIE not in tail:
        problems.append("结尾没有 PyInstaller 归档标记，文件被截断了")
    if size < 20 * 1024 * 1024:
        problems.append(f"体积只有 {size / 1048576:.1f} MB，明显偏小")
    return problems


def is_locked(path: Path) -> bool:
    """正在运行的 exe 会被系统锁住，无法覆盖。"""
    if not path.exists():
        return False
    try:
        with open(path, "r+b"):
            return False
    except OSError:
        return True


def bump_version() -> None:
    """把 core/version.py 里的 PATCH 位 +1。"""
    path = ROOT / "core" / "version.py"
    text = path.read_text(encoding="utf-8")
    new = version.bump_patch()
    text = text.replace(f'VERSION = "{version.VERSION}"', f'VERSION = "{new}"', 1)
    path.write_text(text, encoding="utf-8")
    print(f"版本号 {version.VERSION} → {new}")


def main() -> int:
    args = sys.argv[1:]
    if "--bump" in args:
        bump_version()
        # 重新加载，拿到新版本号
        import importlib

        importlib.reload(version)

    icon = ROOT / "assets" / "icon.ico"
    target = ROOT / "dist" / f"{NAME}.exe"

    # 旧版本还在运行时 dist 里的 exe 删不掉，PyInstaller 会在收尾阶段报错，
    # 整个打包白跑一遍。先探一下，被占用就换个目录输出。
    dist = ROOT / "dist"
    if is_locked(target):
        dist = ROOT / "dist_pending"
        print(f"检测到 {target.name} 正在运行，本次输出到 dist_pending/")

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        # 启动即申请管理员：清理 system32/Temp、读写注册表、结束高权限进程
        # 都要提权，否则用户点下去全是「拒绝访问」。这个开关会写进 exe 的
        # manifest（requireAdministrator），双击时由 Windows 弹 UAC。
        # 注意：本脚本会重写 build/*.spec，所以提权配置必须写在这里，
        # 只改 spec 的话下次打包就被冲掉了。
        "--uac-admin",
        "--name",
        NAME,
        "--distpath",
        str(dist),
        "--workpath",
        str(ROOT / "build"),
        "--specpath",
        str(ROOT / "build"),
    ]
    if icon.exists():
        cmd += ["--icon", str(icon)]
    for module in EXCLUDES:
        cmd += ["--exclude-module", module]
    cmd.append(str(ROOT / "main.py"))

    print("开始打包…")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        print("打包失败")
        return result.returncode

    exe = dist / f"{NAME}.exe"
    if exe.exists():
        problems = verify_exe(exe)
        if problems:
            print("\n打包产物自检失败：")
            for item in problems:
                print("  -", item)
            print("未归档 —— 这份文件发出去同样会报「文件或目录损坏」。")
            return 1

        size = exe.stat().st_size / 1024 / 1024
        digest = releases.sha256_of(exe)
        print(f"\n打包完成：{exe}")
        print(f"文件大小：{size:.1f} MB")
        print(f"SHA256：{digest}")
        print(f"版本：v{version.VERSION}·{version.CHANNEL}")
        print("自检通过：MZ / PE / PyInstaller 归档尾齐全")

        if "--no-archive" not in args:
            try:
                saved = releases.archive(version.VERSION, exe)
                print(f"已归档：{saved}")
                print("分发用 zip：releases/…/" + f"{NAME}-v{version.VERSION}.zip")
            except Exception as exc:
                print(f"归档失败（不影响使用）：{exc}")
        if dist.name == "dist_pending":
            print(
                "旧版本仍在运行，新文件放在了 dist_pending/。\n"
                "关闭所有「Windows系统管家」窗口后，把 dist_pending\\Windows系统管家.exe 覆盖到 dist\\ 即可。"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
