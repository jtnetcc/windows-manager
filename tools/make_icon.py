"""生成应用图标 assets/icon.ico 与 icon.png。

运行：python tools/make_icon.py

图形本体在 `ui/appicon.py`，这里只负责把它渲染成各种尺寸并打包成 .ico。
每个尺寸都**单独渲染**，而不是从 256 缩下去 —— 缩放会把 16px 的笔画糊掉。
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from ui import appicon  # noqa: E402

SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def _png_bytes(size: int) -> bytes:
    image = appicon.render(size)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def build_ico(sizes=SIZES) -> bytes:
    """手工拼 ICO 容器。

    Vista 之后 ICO 里可以直接放 PNG，所以不用转 BMP，
    小尺寸也能保留抗锯齿。
    """
    images = [(size, _png_bytes(size)) for size in sizes]

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries = b""
    payload = b""

    for size, data in images:
        # 宽高各占 1 字节，256 用 0 表示
        dim = 0 if size >= 256 else size
        entries += struct.pack(
            "<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset
        )
        offset += len(data)
        payload += data

    return header + entries + payload


def main() -> int:
    app = QGuiApplication(sys.argv[:1])  # noqa: F841  QImage 需要 QGuiApplication

    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)

    (assets / "icon.png").write_bytes(_png_bytes(256))
    (assets / "icon.ico").write_bytes(build_ico())

    print(f"已生成 {assets / 'icon.png'}（256×256）")
    print(f"已生成 {assets / 'icon.ico'}（{len(SIZES)} 个尺寸）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
