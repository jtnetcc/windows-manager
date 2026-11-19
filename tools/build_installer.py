"""打 Windows 安装包（.exe）。

和 `tools/build.py` 的分工：
    build.py     产出**便携版**  dist/Windows系统管家.exe（单文件，双击就跑）
    build_installer.py 产出**安装包** dist/Windows系统管家-vX.Y.Z-setup.exe

安装包做的事：复制到 %LOCALAPPDATA%\\Programs、建开始菜单/桌面快捷方式、
写「应用和功能」里的卸载登记、留一个 uninstall.exe。全程按用户安装，
不弹 UAC。

运行：

    python tools/build_installer.py              # 用 dist/ 里现成的 exe
    python tools/build_installer.py --build      # 先跑一次 build.py 再打安装包

依赖：只需要 PyInstaller（已在 requirements-build.txt）。**不需要** Inno Setup /
NSIS —— 安装器本身就是 PyInstaller 打的 Qt 小程序（见 installer/installer_app.py）。

产物：
    dist/Windows系统管家-vX.Y.Z-setup.exe
    releases/vX.Y.Z/Windows系统管家-vX.Y.Z-setup.exe（归档一份）
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import version  # noqa: E402
from tools import releases  # noqa: E402

NAME = "Windows系统管家"
DIST = ROOT / "dist"
STAGE = ROOT / "build" / "installer_stage"
PAYLOAD = STAGE / "payload"
WORK = ROOT / "build" / "installer_work"
ICON = ROOT / "assets" / "icon.ico"


def run(cmd: list[str], title: str) -> None:
    print(f"→ {title}")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(f"{title} 失败（exit {result.returncode}）")


def pyinstaller(args: list[str]) -> None:
    base = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean"]
    run(base + args, "PyInstaller")


def stage_payload(app_exe: Path) -> None:
    if PAYLOAD.exists():
        shutil.rmtree(PAYLOAD)
    PAYLOAD.mkdir(parents=True)

    shutil.copy2(app_exe, PAYLOAD / f"{NAME}.exe")
    icon_png = ROOT / "assets" / "icon.png"
    if icon_png.exists():
        shutil.copy2(icon_png, PAYLOAD / "icon.png")

    (PAYLOAD / "manifest.json").write_text(
        json.dumps(
            {
                "name": NAME,
                "version": version.VERSION,
                "channel": version.CHANNEL,
                "exe": f"{NAME}.exe",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    (PAYLOAD / "README.txt").write_text(
        f"""{NAME} {version.text()}

安装位置
  默认 %LOCALAPPDATA%\\Programs\\{NAME}
  卸载：开始菜单里的「卸载 {NAME}」，或 设置 → 应用 → 已安装的应用

数据目录
  %APPDATA%\\MemoryKeeper（配置、统计、日志都在这里，卸载默认保留）

说明
  免费开源，不联网、不上传文件。程序启动时会请求管理员权限，
  因为清理系统目录、结束高权限进程需要它。
""",
        encoding="utf-8",
    )
    print(f"  payload: {len(list(PAYLOAD.iterdir()))} 个文件")


def build_uninstaller() -> Path:
    out = ROOT / "build" / "installer_dist"
    args = [
        "--onefile",
        "--windowed",
        "--name",
        "uninstall",
        "--distpath",
        str(out),
        "--workpath",
        str(WORK / "uninstall"),
        "--specpath",
        str(WORK),
    ]
    if ICON.exists():
        args += ["--icon", str(ICON)]
    # 卸载器不引 Qt：只用到 ctypes 的几个对话框
    args += ["--exclude-module", "PySide6"]
    args.append(str(ROOT / "installer" / "uninstall_app.py"))
    pyinstaller(args)
    exe = out / "uninstall.exe"
    if not exe.exists():
        raise SystemExit("卸载器没生成")
    return exe


def build_setup() -> Path:
    out = ROOT / "build" / "installer_dist"
    args = [
        "--onefile",
        "--windowed",
        "--name",
        "setup",
        "--distpath",
        str(out),
        "--workpath",
        str(WORK / "setup"),
        "--specpath",
        str(WORK),
        "--add-data",
        f"{PAYLOAD}{';' if sys.platform == 'win32' else ':'}payload",
        # 安装不写 Program Files，不需要管理员：省掉一次 UAC，
        # 装到 %LOCALAPPDATA% 也是微软对「按用户安装」的推荐做法
        "--exclude-module",
        "tkinter",
        "--exclude-module",
        "unittest",
        "--exclude-module",
        "PySide6.QtQml",
        "--exclude-module",
        "PySide6.QtQuick",
        "--exclude-module",
        "PySide6.QtQuickWidgets",
        "--exclude-module",
        "PySide6.QtTest",
        "--exclude-module",
        "PySide6.QtNetwork",
    ]
    if ICON.exists():
        args += ["--icon", str(ICON)]
    args.append(str(ROOT / "installer" / "installer_app.py"))
    pyinstaller(args)
    exe = out / "setup.exe"
    if not exe.exists():
        raise SystemExit("安装包没生成")
    return exe


def verify(path: Path) -> None:
    """最小自检：MZ 头 + PE 签名 + 体积。发出去之前别把半截文件给人。"""
    size = path.stat().st_size
    with path.open("rb") as handle:
        head = handle.read(1024)
    if head[:2] != b"MZ":
        raise SystemExit(f"{path.name} 不是有效 PE 文件")
    if size < 5 * 1024 * 1024:
        raise SystemExit(f"{path.name} 只有 {size} 字节，明显不对")
    print(f"  自检通过：{size / 1048576:.1f} MB")


# 判「产物是否陈旧」要看这些源码/资源，不看 docs / tools
SOURCE_GLOBS = ("core/*.py", "ui/*.py", "installer/*.py", "assets/*")
SOURCE_FILES = ("main.py",)


def newest_source_mtime() -> float:
    """源码与资源里最新的修改时间。"""
    newest = 0.0
    for pattern in SOURCE_GLOBS:
        for path in ROOT.glob(pattern):
            if path.is_file():
                newest = max(newest, path.stat().st_mtime)
    for name in SOURCE_FILES:
        path = ROOT / name
        if path.exists():
            newest = max(newest, path.stat().st_mtime)
    return newest


def choose_newest(paths: list[Path]) -> Path:
    """dist 与 dist_pending 里取 mtime 更新的那个。

    build.py 在 dist exe 被占用时会把新产物写到 dist_pending，而 dist 里
    旧的 exe 仍然存在 —— 只判断「dist 存在」就是 v0.0.62 那次事故的根源。
    """
    return max(paths, key=lambda path: path.stat().st_mtime)


def assert_fresh(exe: Path, allow_stale: bool = False) -> None:
    """产物必须比源码新，否则拒绝打包。

    比 mtime 更简单的判断（比如只看文件存在）已经被证明会出事；这里宁可
    让发版流程停下，也不再把旧程序装进新安装包。
    """
    sources = newest_source_mtime()
    if exe.stat().st_mtime >= sources:
        return
    detail = (
        f"选中的便携版比源码旧：{exe}\n"
        f"  exe 修改时间：{time.strftime('%m-%d %H:%M', time.localtime(exe.stat().st_mtime))}\n"
        f"  源码最新时间：{time.strftime('%m-%d %H:%M', time.localtime(sources))}\n"
        "关掉正在运行的程序后重跑 `--build`；确认无误可加 --allow-stale 跳过"
    )
    if allow_stale:
        print(f"警告：{detail}")
        return
    raise SystemExit(detail)


def main() -> int:
    args = sys.argv[1:]
    app_exe = DIST / f"{NAME}.exe"

    if "--build" in args or not app_exe.exists():
        print("先打包便携版…")
        run([sys.executable, "tools/build.py"], "打包便携版")
        # build.py 在 dist 里的 exe 被占用时会改写到 dist_pending，
        # 而 dist 里那份**仍然存在**（是上一版）。以前这里只看 dist 存不存在，
        # 结果把旧 exe 塞进了新安装包 —— v0.0.62 就是这么发的：版本号对了，
        # 里面装的还是 v0.0.61。现在取「两个位置里更新的那个」，并打印来源。
        candidates = [
            path
            for path in (DIST / f"{NAME}.exe", ROOT / "dist_pending" / f"{NAME}.exe")
            if path.exists()
        ]
        if not candidates:
            raise SystemExit("找不到打包好的 exe")
        app_exe = choose_newest(candidates)
        if app_exe.parent.name == "dist_pending":
            print("注意：dist 里的 exe 被占用未更新，本次使用 dist_pending 的新产物")

    assert_fresh(app_exe, allow_stale="--allow-stale" in args)
    print(f"便携版：{app_exe}（{app_exe.stat().st_size / 1048576:.1f} MB）")
    print(f"版本：v{version.VERSION}")

    stage_payload(app_exe)
    print("→ 构建卸载器")
    uninstaller = build_uninstaller()
    shutil.copy2(uninstaller, PAYLOAD / "uninstall.exe")
    print(f"  卸载器：{uninstaller.stat().st_size / 1048576:.1f} MB")

    print("→ 构建安装包")
    setup = build_setup()

    target = DIST / f"{NAME}-v{version.VERSION}-setup.exe"
    shutil.move(str(setup), target)
    verify(target)

    print(f"\n安装包：{target}")
    print(f"文件大小：{target.stat().st_size / 1048576:.1f} MB")
    print(f"SHA256：{releases.sha256_of(target)}")

    # 归档：只把安装包复制进去，不再调用 releases.archive ——
    # 那个函数假定传进来的是便携版 exe，会把它改名成「…-vX.Y.Z.exe」
    dest = ROOT / "releases" / f"v{version.VERSION}"
    try:
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, dest / target.name)
        sums = dest / "SHA256SUMS.txt"
        # 重打同一版本时把旧的同名行去掉，别让校验文件里躺着两条不一样的哈希
        if sums.exists():
            kept = [
                line
                for line in sums.read_text(encoding="utf-8").splitlines()
                if target.name not in line
            ]
            sums.write_text("\n".join(kept) + "\n", encoding="utf-8")
        with sums.open("a", encoding="utf-8") as handle:
            handle.write(f"{releases.sha256_of(target)}  {target.name}\n")
        print(f"已归档：{dest}")
    except OSError as exc:
        print(f"归档失败（不影响使用）：{exc}")

    print("\n静默安装：setup.exe /S        指定目录：setup.exe /S /D=D:\\Tools\\wm")
    return 0


if __name__ == "__main__":
    sys.exit(main())
