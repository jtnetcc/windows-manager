"""把打包好的 exe 同步到阿里云 OSS。

为什么不用 oss2 SDK：这台机器上没装，而 OSS 的简单上传签名就三步
（StringToSign → HMAC-SHA1 → base64），用标准库足够，不用为一次上传引入依赖。

用法：

    set OSS_AK=LTAIxxx
    set OSS_SK=xxx
    python tools/sync_oss.py                 # 上传当前版本 + latest
    python tools/sync_oss.py --dry-run
    python tools/sync_oss.py --private       # 只传不公开

默认把对象设成 public-read，这样链接能直接发给别人下载。
注意：只改这两个对象的 ACL，不动桶本身的权限。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from email.utils import formatdate
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import version  # noqa: E402

BUCKET = os.environ.get("OSS_BUCKET", "jtnetcc")
ENDPOINT = os.environ.get("OSS_ENDPOINT", "jtnetcc.oss-cn-beijing.aliyuncs.com")
PREFIX = "windows-manager"


def sign(method: str, key: str, content_type: str, date: str, oss_headers: dict) -> str:
    """OSS 签名：StringToSign → HMAC-SHA1 → base64。"""
    secret = os.environ["OSS_SK"]
    canonical = "".join(
        f"{k.lower()}:{v}\n" for k, v in sorted(oss_headers.items()) if k.lower().startswith("x-oss-")
    )
    to_sign = f"{method}\n\n{content_type}\n{date}\n{canonical}/{BUCKET}/{key}"
    digest = hmac.new(secret.encode(), to_sign.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def upload(key: str, path: Path, public: bool = True, filename: str = "") -> int:
    body = path.read_bytes()
    content_type = "application/octet-stream"
    date = formatdate(usegmt=True)

    oss_headers = {}
    if public:
        oss_headers["x-oss-object-acl"] = "public-read"

    signature = sign("PUT", key, content_type, date, oss_headers)

    request = urllib.request.Request(
        f"https://{ENDPOINT}/{urllib.parse.quote(key)}", data=body, method="PUT"
    )
    request.add_header("Date", date)
    request.add_header("Content-Type", content_type)
    request.add_header("Authorization", f"OSS {os.environ['OSS_AK']}:{signature}")
    for name, value in oss_headers.items():
        request.add_header(name, value)
    if filename:
        request.add_header(
            "Content-Disposition", f'attachment; filename="{filename}"'
        )

    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        print(f"  失败：{exc.code} {exc.read().decode('utf-8', 'replace')[:200]}")
        return exc.code


def main() -> int:
    parser = argparse.ArgumentParser(description="同步 exe 到阿里云 OSS")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--private", action="store_true", help="不设 public-read")
    args = parser.parse_args()

    for name in ("OSS_AK", "OSS_SK"):
        if not os.environ.get(name):
            print(f"缺少环境变量 {name}")
            return 1

    exe = ROOT / "dist" / "Windows系统管家.exe"
    if not exe.is_file():
        print("没有找到 dist/Windows系统管家.exe，先跑 tools/build.py")
        return 1

    ver = version.VERSION
    size = exe.stat().st_size / 1024 / 1024
    print(f"版本 v{ver}｜{size:.1f} MB")

    targets = [
        (f"{PREFIX}/v{ver}/Windows-Manager-v{ver}.exe", f"Windows-Manager-v{ver}.exe"),
        (f"{PREFIX}/latest/Windows-Manager.exe", "Windows-Manager.exe"),
    ]
    if args.dry_run:
        for key, _ in targets:
            print("  将上传:", key)
        return 0

    public = not args.private
    failed = 0
    for key, filename in targets:
        status = upload(key, exe, public=public, filename=filename)
        mark = "✓" if status == 200 else "✗"
        print(f"  {mark} {key}  ({status})")
        if status != 200:
            failed += 1

    if not failed:
        print("\n下载地址：")
        for key, _ in targets:
            print(f"  https://{ENDPOINT}/{key}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
