"""可选共享评审账号：全站认证与写请求来源检查，不提供多用户文献隔离。"""

import asyncio
import base64
import binascii
import hashlib
import hmac
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

SCRYPT_N = 32768


def password_hash(password):
    if not 12 <= len(password) <= 256:
        raise ValueError("密码须为12—256个字符")
    salt = os.urandom(16)
    digest = derive(password.encode(), salt)
    return f"scrypt:{SCRYPT_N}:8:1:{salt.hex()}:{digest.hex()}"


def derive(password, salt):
    return hashlib.scrypt(
        password, salt=salt, n=SCRYPT_N, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024
    )


def origin(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("访问地址必须是完整的HTTP(S)源，不带路径、账号或查询参数")
    host = parsed.hostname.lower()
    if parsed.scheme == "http" and host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("评审账号须通过HTTPS访问，仅本机回环测试允许HTTP")
    port = parsed.port
    host = f"[{host}]" if ":" in host else host
    if port is not None and port != (443 if parsed.scheme == "https" else 80):
        host += f":{port}"
    return f"{parsed.scheme}://{host}"


@dataclass(frozen=True, repr=False)
class AccessSettings:
    username: str
    public_origin: str
    salt: bytes
    digest: bytes

    @classmethod
    def from_env(cls):
        user = os.getenv("YANXI_AUTH_USER", "")
        encoded = os.getenv("YANXI_AUTH_PASSWORD_HASH", "")
        address = os.getenv("YANXI_AUTH_ORIGIN", "")
        if not any((user, encoded, address)):
            return None
        if not all((user, encoded, address)):
            raise ValueError("评审访问控制必须同时配置账号、密码哈希和访问源")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", user):
            raise ValueError("评审账号只能使用1—64位英文字母、数字、点、下划线或短横线")
        found = re.fullmatch(r"scrypt:32768:8:1:([0-9a-f]{32}):([0-9a-f]{64})", encoded)
        if not found:
            raise ValueError("评审密码哈希格式错误，请用 scripts/hash_password.py 生成")
        return cls(user, origin(address), bytes.fromhex(found[1]), bytes.fromhex(found[2]))

    def verify(self, value):
        try:
            kind, content = value.split(" ", 1)
            if kind.lower() != "basic" or len(content) > 2048:
                return False
            decoded = base64.b64decode(content, validate=True).decode("utf-8")
            user, password = decoded.split(":", 1)
            if len(user) > 64 or len(password) > 256:
                return False
            match = hmac.compare_digest(user.encode(), self.username.encode())
            digest = derive(password.encode(), self.salt)
            return hmac.compare_digest(digest, self.digest) and match
        except (ValueError, UnicodeError, binascii.Error):
            return False


class ReviewAccess:
    def __init__(self, app, settings):
        self.app = app
        self.settings = settings
        self.verifiers = asyncio.Semaphore(4)

    async def __call__(self, scope, receive, send):
        if self.settings is None or scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        if scope["type"] != "http":
            # 当前应用没有WebSocket接口，认证开启后不允许通过其他协议绕开。
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        headers = Headers(scope=scope)

        async def protected_send(message):
            if message["type"] == "http.response.start":
                values = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower()
                    not in {b"cache-control", b"x-frame-options", b"content-security-policy"}
                ]
                values.extend(
                    [
                        (b"cache-control", b"no-store"),
                        (b"vary", b"Authorization"),
                        (b"x-frame-options", b"DENY"),
                        (b"content-security-policy", b"frame-ancestors 'none'; base-uri 'self'"),
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"same-origin"),
                        (b"cross-origin-resource-policy", b"same-origin"),
                    ]
                )
                message = {**message, "headers": values}
            await send(message)

        async def deny(code, detail, challenge=False):
            response = JSONResponse(
                {"detail": detail},
                status_code=code,
                headers={"WWW-Authenticate": 'Basic realm="Yanxi review", charset="UTF-8"'}
                if challenge
                else None,
            )
            await response(scope, receive, protected_send)

        if scope.get("scheme") != urlsplit(self.settings.public_origin).scheme:
            return await deny(426, "请通过配置的HTTPS评审入口访问")
        host = headers.getlist("host")
        try:
            expected_host = urlsplit(self.settings.public_origin).netloc
            valid_host = (
                len(host) == 1
                and urlsplit(
                    origin(f"{urlsplit(self.settings.public_origin).scheme}://{host[0]}")
                ).netloc
                == expected_host
            )
        except ValueError:
            valid_host = False
        if not valid_host:
            return await deny(400, "访问地址与评审入口配置不一致")
        credentials = headers.getlist("authorization")
        valid = False
        if len(credentials) == 1:
            async with self.verifiers:
                valid = await asyncio.to_thread(self.settings.verify, credentials[0])
        if not valid:
            return await deny(401, "请使用评审账号登录", challenge=True)
        if headers.get("sec-fetch-site", "") in {"cross-site", "same-site"} and not (
            scope["method"] in {"GET", "HEAD"}
            and headers.get("sec-fetch-mode") == "navigate"
            and headers.get("sec-fetch-dest") == "document"
        ):
            return await deny(403, "已拒绝来自其他站点的资源请求")
        unsafe = scope["method"] not in {"GET", "HEAD", "OPTIONS"}
        if unsafe or scope["path"] == "/api/discovery/search":
            if headers.getlist("x-yanxi-request") != ["1"]:
                return await deny(403, "此操作需要由研析页面或带请求标记的客户端发起")
        if unsafe:
            supplied = headers.getlist("origin")
            try:
                valid_origin = not supplied or (
                    len(supplied) == 1 and origin(supplied[0]) == self.settings.public_origin
                )
            except ValueError:
                valid_origin = False
            if not valid_origin or headers.get("sec-fetch-site", "") in {"cross-site", "same-site"}:
                return await deny(403, "已拒绝来自其他站点的修改请求")
        return await self.app(scope, receive, protected_send)
