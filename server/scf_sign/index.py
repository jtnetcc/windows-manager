"""扫码函数：给客户端换一个短时效的下载签名。

部署在腾讯云 SCF（Python 3.10）+ API 网关，无状态、按调用计费。
它做三件事，别的什么都不做：

    GET /latest    → {"version", "notes", "size", "url", "expires"}
    GET /download  → {"url", "expires"}
    GET /health    → {"ok": true}

`url` 是 OSS 的签名地址，默认 10 分钟有效。桶设成私有之后，直链本身
拉不动文件，只有这里现签的地址能用 —— 地址被转发出去，过一会儿就废了。

环境变量（在函数配置页填）：

    OSS_AK       子账号 AccessKey，**只授这个 bucket 的 GetObject**
    OSS_SK       对应 Secret
    OSS_BUCKET   桶名
    OSS_ENDPOINT 虚拟主机风格端点，例如 https://<bucket>.oss-cn-beijing.aliyuncs.com
    SIGN_TTL     签名有效期（秒），默认 600

安全说明：函数的地址本身就是公开的，谁都能调。它能做到的只是「换签名」，
不是「防住所有人」：想再收紧就去 API 网关配限流（比如 60 次/分钟/IP），
或者加一个固定 header 校验 —— 但 header 也写在 exe 里，只能挡随手写脚本的人。
"""

from __future__ import annotations

import json
import os
import time

import oss2

BUCKET = os.environ.get("OSS_BUCKET", "")
ENDPOINT = os.environ.get("OSS_ENDPOINT", "")
TTL = int(os.environ.get("SIGN_TTL", "600"))

# 只允许签这两个对象。坚决不做「按参数签任意 key」——
# 那等于把这个桶的所有文件都开放出去。
MANIFEST_KEY = "windows-manager/latest/version.json"
EXE_KEY = "windows-manager/latest/Windows-Manager.exe"

_bucket = None
_manifest_cache = {"data": None, "at": 0.0}
_MANIFEST_TTL = 30        # 清单缓存 30 秒，避免每次检查更新都打一次 OSS


def _get_bucket():
    global _bucket
    if _bucket is None:
        auth = oss2.Auth(os.environ["OSS_AK"], os.environ["OSS_SK"])
        _bucket = oss2.Bucket(auth, ENDPOINT, BUCKET)
    return _bucket


def _sign(key: str) -> dict:
    """签一个 GET 地址。slash_safe=True：URL 里的 / 不参与编码。"""
    url = _get_bucket().sign_url("GET", key, TTL, slash_safe=True)
    return {"url": url, "expires": TTL}


def _manifest() -> dict:
    now = time.time()
    if _manifest_cache["data"] and now - _manifest_cache["at"] < _MANIFEST_TTL:
        return dict(_manifest_cache["data"])

    raw = _get_bucket().get_object(MANIFEST_KEY).read().decode("utf-8")
    data = json.loads(raw)
    _manifest_cache["data"] = data
    _manifest_cache["at"] = now
    return dict(data)


def _reply(status: int, payload: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": "no-store",
            "Access-Control-Allow-Origin": "*",
        },
        "isBase64Encoded": False,
        "body": json.dumps(payload, ensure_ascii=False),
    }


def _path(event: dict) -> str:
    """取请求路径。

    API 网关会带上发布环境前缀（`/release/latest`、`/test/download`），
    所以只认最后一段，别指望路径是干净的 `/latest`。
    """
    path = event.get("path") or ""
    if not path:
        path = (event.get("requestContext") or {}).get("path") or ""
    if not path:
        query = event.get("queryString") or event.get("queryStringParameters") or {}
        path = str(query.get("action") or "")
    segments = [part for part in path.split("/") if part]
    return "/" + (segments[-1] if segments else "")


def main_handler(event: dict, context) -> dict:
    route = _path(event)

    if route == "/health":
        return _reply(200, {"ok": True, "ttl": TTL})

    if route == "/download":
        return _reply(200, _sign(EXE_KEY))

    if route == "/latest":
        try:
            data = _manifest()
        except Exception as exc:  # noqa: BLE001 — 函数里任何异常都要变成可读的 JSON
            return _reply(502, {"error": f"读不到版本清单：{exc}"})
        payload = {
            "version": str(data.get("version", "")).strip(),
            "notes": str(data.get("notes", "")).strip(),
            "size": str(data.get("size", "")).strip(),
        }
        payload.update(_sign(EXE_KEY))
        return _reply(200, payload)

    return _reply(404, {"error": f"未知路径 {route}，只支持 /latest /download /health"})
