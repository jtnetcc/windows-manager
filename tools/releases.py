"""版本归档与回滚。

每次打包都会把「源码 + exe + 分发 zip + 校验和」存进 `releases/vX.Y.Z/`，
回滚就是把它盖回来。

为什么要连 zip 一起存：微信 / QQ 传 38 MB 的单文件 exe，接收端偶尔只落地一部分，
双击报的是「文件或目录损坏且无法读取」（0x80070570），看起来像程序坏了。zip 自带
CRC，解压时就报错；SHA256SUMS.txt 则让「手里这份是不是完整」有一个可比对的答案。

设计上刻意不用 git：这个项目从没有版本控制，用户也不一定熟悉 git 操作。
一个能列版本、能一键回滚的脚本，比一堆 git 命令更实用。

用法：

    python tools/releases.py              列出所有版本
    python tools/releases.py --to 0.0.1   回滚到 v0.0.1
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASES = ROOT / "releases"
NAME = "Windows系统管家"

# 归档哪些内容（相对 ROOT）。build/dist/releases 不归档，越滚越大。
ARCHIVE_DIRS = ("core", "ui", "tools", "assets", "docs")
ARCHIVE_FILES = ("main.py", "requirements.txt")
SKIP_PARTS = {"__pycache__", ".pytest_cache"}

# 放进分发 zip 的说明。用户拿到的第一手信息就在这里，别写成免责声明合集。
README = """{name} v{version} · 免费开源

怎么用
  1. 解压到一个本地文件夹（例如 D:\\Tools\\{name}\\）
  2. 双击 {name}.exe，Windows 会请求管理员权限，点「是」
  3. 别在解压软件的临时目录里运行，也别直接从微信 / QQ 的接收目录双击

文件完整性
  命令行执行：
      certutil -hashfile "{name}.exe" SHA256
  期望值：
      {sha256}

  如果双击报「文件或目录损坏且无法读取」，那是文件在传输或下载过程中不完整，
  与本软件无关：重新复制一次即可。本压缩包带 CRC 校验，解压报错就说明包坏了。

关于
  免费开源：不收费、无广告、不捆绑、不联网，不上传任何文件。
  数据目录：%APPDATA%\\MemoryKeeper（配置与统计都只存在本机）
"""


def sha256_of(path: Path) -> str:
    """分块算，别把 38 MB 一次读进内存。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def release_dir(version: str) -> Path:
    return RELEASES / f"v{version}"


def archive(version: str, exe: Path, note: str = "") -> Path:
    """把当前源码和 exe 存进 releases/vX.Y.Z/，返回目录。"""
    target = release_dir(version)
    target.mkdir(parents=True, exist_ok=True)

    # 1) 源码 zip
    zip_path = target / "source.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in ARCHIVE_DIRS:
            folder = ROOT / name
            if not folder.is_dir():
                continue
            for path in folder.rglob("*"):
                if not path.is_file():
                    continue
                if any(part in SKIP_PARTS for part in path.parts):
                    continue
                zf.write(path, path.relative_to(ROOT).as_posix())
        for name in ARCHIVE_FILES:
            path = ROOT / name
            if path.is_file():
                zf.write(path, name)

    # 2) exe 副本（带版本号，方便直接发给人）+ 分发 zip + 校验和
    manifest = {
        "version": version,
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": note,
        "source_files": 0,
        "exe_mb": 0,
    }
    if exe.exists():
        exe_copy = target / f"{NAME}-v{version}.exe"
        shutil.copy2(exe, exe_copy)
        exe_hash = sha256_of(exe_copy)

        bundle = target / f"{NAME}-v{version}.zip"
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(exe_copy, f"{NAME}.exe")
            zf.writestr(
                "README.txt",
                README.format(name=NAME, version=version, sha256=exe_hash),
            )
        zip_hash = sha256_of(bundle)

        (target / "SHA256SUMS.txt").write_text(
            f"{exe_hash}  {exe_copy.name}\n{zip_hash}  {bundle.name}\n",
            encoding="utf-8",
        )
        manifest.update(
            exe_mb=round(exe_copy.stat().st_size / 1048576, 1),
            exe_bytes=exe_copy.stat().st_size,
            exe_sha256=exe_hash,
            zip_mb=round(bundle.stat().st_size / 1048576, 1),
            zip_sha256=zip_hash,
        )

    # 3) 清单
    with zipfile.ZipFile(zip_path) as zf:
        manifest["source_files"] = len(zf.namelist())
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target


def list_versions() -> list[dict]:
    """按版本号从新到旧列出。"""
    out: list[dict] = []
    if not RELEASES.is_dir():
        return out
    for folder in RELEASES.iterdir():
        if not folder.is_dir() or not folder.name.startswith("v"):
            continue
        manifest = folder / "manifest.json"
        data = {"version": folder.name[1:], "built_at": "—", "note": "", "exe_mb": 0}
        if manifest.is_file():
            try:
                data.update(json.loads(manifest.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass
        out.append(data)

    def sort_key(item: dict):
        try:
            return tuple(int(x) for x in item["version"].split("."))
        except ValueError:
            return (0, 0, 0)

    out.sort(key=sort_key, reverse=True)
    return out


def rollback(version: str) -> tuple[bool, str]:
    """把指定版本的源码和 exe 恢复回来。当前状态会先备份。"""
    target = release_dir(version)
    zip_path = target / "source.zip"
    if not zip_path.is_file():
        return False, f"v{version} 没有源码快照"

    # 先把当前状态存一份，回滚本身也要能反悔
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safety = RELEASES / f"_rollback_{stamp}"
    safety.mkdir(parents=True, exist_ok=True)
    current_zip = safety / "source.zip"
    with zipfile.ZipFile(current_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in ARCHIVE_DIRS:
            folder = ROOT / name
            if not folder.is_dir():
                continue
            for path in folder.rglob("*"):
                if path.is_file() and not any(p in SKIP_PARTS for p in path.parts):
                    zf.write(path, path.relative_to(ROOT).as_posix())
        for name in ARCHIVE_FILES:
            path = ROOT / name
            if path.is_file():
                zf.write(path, name)

    # 恢复源码。只覆盖 zip 里有的文件，不删除多出来的新文件
    # （删掉的话可能把回滚后新增的模块一起干掉）
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(ROOT)

    # 恢复 exe
    exe_src = target / f"Windows系统管家-v{version}.exe"
    message = f"源码已恢复到 v{version}"
    if exe_src.is_file():
        exe_dst = ROOT / "dist" / "Windows系统管家.exe"
        try:
            shutil.copy2(exe_src, exe_dst)
            message += "，exe 也已替换"
        except OSError:
            message += "，但 exe 被占用没能替换（关掉程序后手动复制）"
    message += f"\n当前状态已备份到 {safety.name}"
    _prune_safety(keep=3)
    return True, message


def _prune_safety(keep: int = 3) -> None:
    """回滚前备份只保留最近几份，不然每回滚一次就多一份 240 KB。"""
    folders = sorted(
        (p for p in RELEASES.iterdir() if p.is_dir() and p.name.startswith("_rollback_")),
        key=lambda p: p.name,
        reverse=True,
    )
    for old in folders[keep:]:
        shutil.rmtree(old, ignore_errors=True)


def main() -> int:
    args = sys.argv[1:]
    if "--to" in args:
        index = args.index("--to")
        if index + 1 >= len(args):
            print("用法：python tools/releases.py --to 0.0.1")
            return 1
        version = args[index + 1].lstrip("v")
        ok, message = rollback(version)
        print(message)
        return 0 if ok else 1

    versions = list_versions()
    if not versions:
        print("还没有任何归档版本。跑一次 python tools/build.py 就会生成。")
        return 0

    print(f"{'版本':<10}{'构建时间':<22}{'文件数':<8}{'exe':<8}备注")
    print("-" * 66)
    for item in versions:
        print(
            f"v{item['version']:<9}{item['built_at']:<22}"
            f"{item.get('source_files', 0):<8}{str(item.get('exe_mb', 0)) + ' MB':<8}"
            f"{item.get('note', '')}"
        )
    print(f"\n回滚：python tools/releases.py --to {versions[-1]['version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
