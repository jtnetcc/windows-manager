"""打 macOS 安装包（.app + .dmg）。

**必须在 macOS 上运行** —— .app 的目录结构、代码签名、hdiutil 都是 macOS
自带的，无法在 Windows 上交叉产出（PyInstaller 也不支持交叉编译）。

    python tools/build_macos.py                # 出 .app 和 .dmg
    python tools/build_macos.py --no-dmg       # 只出 .app（调试用）
    CODESIGN_IDENTITY="Developer ID Application: xxx" python tools/build_macos.py

产物：
    dist/Windows系统管家.app
    dist/Windows系统管家-vX.Y.Z.dmg

⚠️ 关于这个 .dmg 能不能用，先说清楚：**当前代码是 Windows 专用的**，
   注册表、WMI、DeviceIoControl、命名互斥体这些依赖没有 macOS 实现。
   打出来的 .app 能安装、能启动，但 main.py 的 `_require_windows()` 会
   弹一句提示然后退出 —— 它是「能装、但明确告知不支持」的壳子，
   不是能用的 macOS 版。真正的移植清单见 docs/打包说明.md。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import version  # noqa: E402

NAME = "Windows系统管家"
BUNDLE_ID = "com.jtnetcc.windowsmanager"
DIST = ROOT / "dist"
BUILD = ROOT / "build" / "macos"
ICON = ROOT / "assets" / "icon.icns"

EXCLUDES = [
    "tkinter",
    "unittest",
    "pydoc",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtSql",
    "PySide6.QtXml",
]


def run(cmd: list[str], title: str) -> None:
    print(f"→ {title}")
    if subprocess.run(cmd, cwd=ROOT).returncode != 0:
        raise SystemExit(f"{title} 失败")


def ensure_icon() -> Path:
    if ICON.exists():
        return ICON
    run([sys.executable, "tools/make_icns.py"], "生成 icon.icns")
    return ICON


def build_app() -> Path:
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",              # 生成 .app 而不是裸二进制
        "--onedir",                # macOS 上 onefile 会被系统反复解包，慢且容易被 Gatekeeper 拦
        "--name",
        NAME,
        "--icon",
        str(ensure_icon()),
        "--osx-bundle-identifier",
        BUNDLE_ID,
        "--distpath",
        str(DIST),
        "--workpath",
        str(BUILD / "work"),
        "--specpath",
        str(BUILD),
    ]
    for module in EXCLUDES:
        args += ["--exclude-module", module]
    args.append(str(ROOT / "main.py"))
    run(args, "PyInstaller 打包 .app")

    app = DIST / f"{NAME}.app"
    if not app.exists():
        raise SystemExit("没有生成 .app")
    return app


def sign(app: Path) -> None:
    """有证书就签，没有就跳过（未签名的 app 在别人机器上会被 Gatekeeper 拦）。"""
    identity = os.environ.get("CODESIGN_IDENTITY", "").strip()
    if not identity:
        print("→ 跳过签名（设置 CODESIGN_IDENTITY 可自动签）")
        return
    run(
        ["codesign", "--force", "--deep", "--options", "runtime",
         "--sign", identity, str(app)],
        f"签名 {identity}",
    )


def build_dmg(app: Path) -> Path:
    stage = BUILD / "dmg"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    shutil.copytree(app, stage / app.name, symlinks=True)
    # 拖拽安装的惯例：旁边放一个「应用程序」的替身
    link = stage / "Applications"
    if not link.exists():
        link.symlink_to("/Applications")

    dmg = DIST / f"{NAME}-v{version.VERSION}.dmg"
    if dmg.exists():
        dmg.unlink()

    run(
        [
            "hdiutil", "create",
            "-volname", f"{NAME} {version.VERSION}",
            "-srcfolder", str(stage),
            "-ov", "-format", "UDZO",
            str(dmg),
        ],
        "生成 .dmg",
    )
    return dmg


def main() -> int:
    if sys.platform != "darwin":
        raise SystemExit(
            "这个脚本只能在 macOS 上跑：.app 结构与 hdiutil 都是系统自带的，"
            "无法在 Windows/Linux 上交叉产出。"
        )

    print(f"版本：v{version.VERSION}·{version.CHANNEL}  架构：{os.uname().machine}")
    app = build_app()
    sign(app)
    print(f"已生成 {app}")

    if "--no-dmg" not in sys.argv[1:]:
        dmg = build_dmg(app)
        size = dmg.stat().st_size / 1048576
        digest = subprocess.run(
            ["shasum", "-a", "256", str(dmg)], capture_output=True, text=True
        ).stdout.split()[0]
        print(f"已生成 {dmg}（{size:.1f} MB）")
        print(f"SHA256：{digest}")

    print("\n提醒：这是 Windows 专用程序的 macOS 壳，运行时只会提示不支持。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
