"""签名下载链路的断言。

不联网：把 `updater._fetch` 换成一个假的，只验证「换签—缓存—过期重取—退路」
这四步的逻辑。真正的连通性要等云函数部署完再用 curl 验（见 README）。

    python tools/check_sign.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import updater  # noqa: E402


def reset() -> None:
    updater._ticket = None
    updater._LAST = None
    updater._SIGN_BASE = ""


def main() -> None:
    calls: list[str] = []

    def fake_fetch(url: str, use_proxy: bool, timeout: float = 3) -> dict:
        calls.append(url)
        if "sign.test" in url:
            if url.endswith("/latest"):
                return {
                    "version": "9.9.9",
                    "notes": "假数据",
                    "size": "1",
                    "url": "https://bucket.example/exe?Signature=abc",
                    "expires": 600,
                }
            if url.endswith("/download"):
                return {"url": "https://bucket.example/exe?Signature=abc", "expires": 600}
            raise RuntimeError(f"未预期的路径：{url}")
        raise OSError("没配签名服务时不该走这条路")

    real_fetch = updater._fetch
    updater._fetch = fake_fetch          # type: ignore[assignment]

    try:
        # 1. 没配签名服务：退回直链，不报错
        reset()
        direct = updater.download_url()
        assert direct.startswith("https://") and "Signature" not in direct, direct
        assert not calls, f"没配签名服务却发了请求：{calls}"
        print("未部署签名服务时退回直链 ✓")

        # 2. 配了服务：拿到签名地址
        reset()
        updater._SIGN_BASE = "https://sign.test/"
        calls.clear()
        url = updater.download_url()
        assert url.endswith("Signature=abc"), url
        assert calls == ["https://sign.test/download"], calls
        print("换签成功 ✓")

        # 3. 有效期内复用缓存，不再请求
        calls.clear()
        again = updater.download_url()
        assert again == url and not calls, (again, calls)
        print("有效期内命中缓存 ✓")

        # 4. 快过期就提前重取（不能等到最后一秒）
        calls.clear()
        updater._ticket.expires_at = time.monotonic() + 30    # 只剩 30 秒 < 提前量 60
        updater.download_url()
        assert calls == ["https://sign.test/download"], calls
        print("临近过期自动重取 ✓")

        # 5. 检查更新问 /latest，一次拿到版本号 + 签名地址
        reset()
        updater._SIGN_BASE = "https://sign.test/"
        calls.clear()
        info = updater.check()
        assert info.ok and info.latest == "9.9.9", info
        assert info.url.endswith("Signature=abc"), info.url
        assert calls == ["https://sign.test/latest"], calls
        print("check() 走签名服务 ✓")

        # 6. 服务挂了：退回直链，不抛异常
        reset()
        updater._SIGN_BASE = "https://sign.test/"

        def dead(url: str, use_proxy: bool, timeout: float = 3) -> dict:
            raise OSError("连不上")

        updater._fetch = dead         # type: ignore[assignment]
        assert updater.download_url().startswith("https://"), "换签失败要有退路"
        info = updater.check()
        assert not info.ok and info.error, info
        print("服务不可用时有退路 ✓")

        # 7. 渠道里存的是取值函数而不是字符串：签名十分钟过期，
        #    写成字符串就会在程序开着放一会儿之后变死链。
        from ui import page_about

        for channel in page_about.CHANNELS:
            assert callable(channel.url), f"渠道 {channel.name} 存的是字符串"
        print(f"渠道 {len(page_about.CHANNELS)} 个 · 地址按下时现取 ✓")

    finally:
        updater._fetch = real_fetch      # type: ignore[assignment]
        reset()

    print("签名链路 OK")


if __name__ == "__main__":
    main()
