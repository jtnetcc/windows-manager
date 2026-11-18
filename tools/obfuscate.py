"""把明文地址混淆成可以直接贴进 core/updater.py 的 bytes()。

用法：

    python tools/obfuscate.py https://abc.example.com/            # 分发域名
    python tools/obfuscate.py https://xxx.apigw.tencentcs.com/ --key sign

输出形如

    _SIGN_ENC = bytes(
        (
            0x3C, 0xA7, ...
        )
    )

把它整段替换掉 updater.py 里对应的 `_ENC` / `_SIGN_ENC` 即可。

注意：**这不是加密**，只是让 `strings 软件.exe` 搜不到域名。真正防流量被白刷
的是私有桶 + 短时效签名（见 server/scf_sign/README.md）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import updater  # noqa: E402


def wrap(raw: bytes, name: str) -> str:
    rows = []
    for i in range(0, len(raw), 8):
        chunk = ", ".join(f"0x{b:02X}" for b in raw[i : i + 8])
        rows.append(f"        {chunk},")
    body = "\n".join(rows)
    return f"{name} = bytes(\n    (\n{body}\n    )\n)"


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        raise SystemExit(2)

    text = args[0].strip()
    key_name = "sign" if "--key" in sys.argv and "sign" in sys.argv else "host"
    if "--key" in sys.argv:
        index = sys.argv.index("--key")
        if index + 1 < len(sys.argv):
            key_name = sys.argv[index + 1].strip()

    key = updater._SIGN_KEY if key_name == "sign" else updater._KEY
    data = text.encode("utf-8")
    enc = bytes(byte ^ key[i % len(key)] for i, byte in enumerate(data))

    target = "_SIGN_ENC" if key_name == "sign" else "_ENC"
    print(f"# {len(data)} 字节，密钥 {key_name}")
    print(f"# 明文：{text}")
    print(wrap(enc, target))

    # 立刻校验一遍，避免贴错
    assert (
        bytes(byte ^ key[i % len(key)] for i, byte in enumerate(enc)).decode() == text
    ), "还原失败，检查是否用了同一把密钥"
    print("# 还原校验通过")


if __name__ == "__main__":
    main()
