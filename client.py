"""Overstats HTTP client. No backend deployment or local backend files required."""

from urllib.parse import urlsplit, urlunsplit

import base64
import json

import aiohttp


class BackendError(Exception):
    def __init__(self, message: str, code: str = "", status: int = 0):
        super().__init__(message)
        self.code = code
        self.status = status


def normalize_url(value: str) -> str:
    value = value.strip().rstrip("/")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("api_url 必须为 http:// 或 https:// 地址")
    if parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError("api_url 不支持查询参数、片段或内嵌账号密码")
    return value + "/api/v2" if parts.path in {"", "/"} else value


def image_bytes(data: bytes) -> bytes:
    valid = (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith(b"\xff\xd8\xff")
        or data.startswith((b"GIF87a", b"GIF89a"))
        or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
    )
    if not valid:
        raise BackendError("后端没有返回有效的 PNG/JPEG/GIF/WebP 图片", "invalid_image")
    return data


class OverstatsClient:
    def __init__(
        self, api_url: str, max_mb: int = 20, *, username: str = "", password: str = ""
    ):
        self.base_url = normalize_url(api_url)
        self.max_bytes = max_mb * 1024 * 1024
        if ":" in username:
            raise ValueError("后端验证账号不能包含冒号")
        self.auth_credentials = (username, password)
        self.session = aiohttp.ClientSession(
            auth=aiohttp.BasicAuth(username, password) if username else None,
            timeout=aiohttp.ClientTimeout(total=600, connect=20),
            connector=aiohttp.TCPConnector(limit=16),
        )

    async def close(self):
        await self.session.close()

    @property
    def root_url(self) -> str:
        return self.base_url.removesuffix("/api/v2")

    async def _read(self, response: aiohttp.ClientResponse) -> bytes:
        body = bytearray()
        async for chunk in response.content.iter_chunked(64 * 1024):
            body.extend(chunk)
            if len(body) > self.max_bytes:
                raise BackendError(
                    "后端响应超过 max_response_mb 限制", "response_too_large"
                )
        return bytes(body)

    async def request(
        self,
        endpoint: str,
        payload: dict | None = None,
        *,
        timeout: int = 600,
        method: str = "POST",
        absolute: bool = False,
    ) -> bytes:
        url = endpoint if absolute else self.base_url + endpoint
        async with self.session.request(
            method,
            url,
            json=payload if method == "POST" else None,
            timeout=aiohttp.ClientTimeout(total=timeout, connect=20),
            allow_redirects=False,
        ) as response:
            body = await self._read(response)
            if response.status != 200:
                try:
                    error = json.loads(body)
                except (ValueError, UnicodeError):
                    error = {}
                if not isinstance(error, dict):
                    error = {}
                message = str(
                    error.get("message")
                    or error.get("detail")
                    or f"HTTP {response.status}"
                )
                if response.status == 401:
                    message = "后端要求身份验证或账号密码不正确，请检查后端验证账号和密码（仅支持 HTTP Basic Auth）"
                elif response.status == 403:
                    message = "后端拒绝访问，请检查反向代理访问权限；网页验证码或登录表单需要另外适配"
                if "Could not resolve customerToken" in message:
                    message = "无法解析战网账号，请检查 BattleTag 的大小写、数字后缀和账号公开状态"
                raise BackendError(
                    message[:800], str(error.get("error", "")), response.status
                )
            return body

    async def image(
        self, endpoint: str, payload: dict | None = None, timeout: int = 600
    ) -> bytes:
        body = await self.request(endpoint, payload or {}, timeout=timeout)
        if body.lstrip().lower().startswith((b"<!doctype html", b"<html")):
            raise BackendError(
                "后端返回了网页而不是战绩图片，可能是登录页面；当前账号密码配置仅支持 HTTP Basic Auth"
            )
        return image_bytes(body)

    async def post_json(self, endpoint: str, payload: dict, timeout: int = 600) -> dict:
        body = await self.request(endpoint, payload, timeout=timeout)
        try:
            data = json.loads(body)
        except (ValueError, UnicodeError) as exc:
            raise BackendError("后端返回的 JSON 无效", "invalid_json") from exc
        if not isinstance(data, dict):
            raise BackendError("后端 JSON 应为对象", "invalid_json")
        if data.get("ok") is False:
            raise BackendError(
                str(data.get("message") or "后端处理失败"), str(data.get("error", ""))
            )
        return data

    async def health(self) -> str:
        body = await self.request(
            self.root_url + "/healthz", method="GET", absolute=True, timeout=15
        )
        return body.decode("utf-8", errors="replace")[:1500]

    async def reply_image(self, item: str | dict) -> bytes:
        raw = ""
        explicit_base64 = False
        if isinstance(item, dict):
            raw = str(item.get("base64") or "")
            explicit_base64 = bool(raw)
            if not raw:
                raw = str(
                    next(
                        (
                            item[k]
                            for k in ("url", "image", "src", "path", "file")
                            if item.get(k)
                        ),
                        "",
                    )
                )
        elif isinstance(item, str):
            raw = item
        raw = raw.strip()
        if not raw:
            raise BackendError("后端返回了空图片", "invalid_image")
        if raw.startswith("base64://"):
            raw = raw[9:]
            explicit_base64 = True
        if raw.startswith("data:image") and ";base64," in raw:
            raw = raw.split(";base64,", 1)[1]
            explicit_base64 = True
        # JPEG base64 often starts with /9j/: try raw base64 before treating / as a URL.
        if explicit_base64 or not raw.startswith(("http://", "https://")):
            if len(raw) > (self.max_bytes * 4 // 3 + 8):
                raise BackendError("图片超过大小限制", "response_too_large")
            try:
                decoded = base64.b64decode(raw + "=" * (-len(raw) % 4), validate=True)
                return image_bytes(decoded)
            except (ValueError, BackendError):
                if explicit_base64:
                    raise BackendError("后端图片 base64 无效", "invalid_image")
        # 下载仅允许配置的后端来源；相对路径以去除 /api/v2 的后端根路径解析。
        if (
            raw.startswith(("http://", "https://", "/"))
            or "/" in raw[:30]
            and raw.endswith((".png", ".jpg", ".webp"))
        ):
            url = (
                raw
                if raw.startswith(("http://", "https://"))
                else self.root_url + "/" + raw.lstrip("/")
            )
            target, origin = urlsplit(url), urlsplit(self.base_url)
            if (target.scheme, target.hostname, target.port) != (
                origin.scheme,
                origin.hostname,
                origin.port,
            ):
                raise BackendError(
                    "后端图片 URL 与配置地址来源不同，请让后端返回 base64 或同来源地址",
                    "image_origin",
                )
            if target.username or target.password or target.fragment:
                raise BackendError("后端图片 URL 无效", "image_url")
            url = urlunsplit(target)
            return image_bytes(
                await self.request(url, method="GET", absolute=True, timeout=60)
            )
        if len(raw) > (self.max_bytes * 4 // 3 + 8):
            raise BackendError("图片超过大小限制", "response_too_large")
        try:
            data = base64.b64decode(raw + "=" * (-len(raw) % 4), validate=True)
        except ValueError as exc:
            raise BackendError("后端图片 base64 无效", "invalid_image") from exc
        return image_bytes(data)
