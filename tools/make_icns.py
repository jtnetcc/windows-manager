"""把 assets/icon.png 生成 macOS 用的 .icns。

为什么自己做而不是用 iconutil：iconutil 只有 macOS 自带，在 Windows 上
打包（或跑 CI 的 windows runner）就断链了。ICNS 的容器格式很简单 ——
`icns` + 总长度 + 一串「4 字节类型 + 4 字节长度 + PNG 数据」——
用 Qt 缩放、用标准库拼字节即可。

用法：
    python tools/make_icns.py            # → assets/icon.icns
    python tools/make_icns.py 源.png 目标.icns
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 类型 → 像素尺寸。同一个尺寸可以有多条（1x / @2x 各一条），macOS 会自己挑。
SLOTS = [
    ("icp4", 16),
    ("icp5", 32),
    ("icp6", 64),
    ("ic07", 128),
    ("ic08", 256),
    ("ic09", 512),
    ("ic10", 1024),
    ("ic11", 32),     # 16 @2x
    ("ic12", 64),     # 32 @2x
    ("ic13", 256),    # 128 @2x
    ("ic14", 512),    # 256 @2x
]


def png_bytes(source: Path, size: int) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, Qt
    from PySide6.QtGui import QImage

    image = QImage(str(source))
    if image.isNull():
        raise SystemExit(f"读不出图片：{source}")
    scaled = image.scaled(
        size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation
    )
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QBuffer.WriteOnly)
    scaled.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def build(source: Path, target: Path) -> int:
    entries = []
    for kind, size in SLOTS:
        payload = png_bytes(source, size)
        entries.append(kind.encode("ascii") + struct.pack(">I", len(payload) + 8) + payload)

    body = b"".join(entries)
    blob = b"icns" + struct.pack(">I", len(body) + 8) + body
    target.write_bytes(blob)
    return len(blob)


def main() -> int:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "assets" / "icon.png"
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "assets" / "icon.icns"
    size = build(source, target)
    print(f"已生成 {target}（{size / 1024:.1f} KB，{len(SLOTS)} 个尺寸）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
