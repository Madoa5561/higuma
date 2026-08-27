from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from threading import Lock
from typing import Any

from .exceptions import Forbidden, TooManyRequests
from .request import Request, request
from .response import ResponseValue

_USER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$")


class PasswordHasher:
    def __init__(
        self,
        *,
        n: int = 2**14,
        r: int = 8,
        p: int = 1,
        salt_bytes: int = 16,
        key_bytes: int = 32,
    ) -> None:
        if not isinstance(n, int) or isinstance(n, bool) or n < 2**10 or n > 2**20 or n & (n - 1):
            raise ValueError("n must be a power of two between 2**10 and 2**20")
        if not isinstance(r, int) or isinstance(r, bool) or not 1 <= r <= 32:
            raise ValueError("r must be an integer between 1 and 32")
        if not isinstance(p, int) or isinstance(p, bool) or not 1 <= p <= 16:
            raise ValueError("p must be an integer between 1 and 16")
        if (
            not isinstance(salt_bytes, int)
            or isinstance(salt_bytes, bool)
            or not 8 <= salt_bytes <= 64
        ):
            raise ValueError("salt_bytes must be an integer between 8 and 64")
        if (
            not isinstance(key_bytes, int)
            or isinstance(key_bytes, bool)
            or not 16 <= key_bytes <= 128
        ):
            raise ValueError("key_bytes must be an integer between 16 and 128")
        self.n = n
        self.r = r
        self.p = p
        self.salt_bytes = salt_bytes
        self.key_bytes = key_bytes

    def hash(self, password: str) -> str:
        if not password:
            raise ValueError("password must not be empty")
        if len(password.encode("utf-8")) > 1024:
            raise ValueError("password must not exceed 1024 UTF-8 bytes")
        salt = secrets.token_bytes(self.salt_bytes)
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=self.n,
            r=self.r,
            p=self.p,
            dklen=self.key_bytes,
        )
        return "$".join(
            (
                "higuma",
                "scrypt",
                str(self.n),
                str(self.r),
                str(self.p),
                _b64(salt),
                _b64(digest),
            )
        )

    def verify(self, password: str, encoded: str) -> bool:
        if len(encoded) > 512 or len(password.encode("utf-8")) > 1024:
            return False
        try:
            marker, algorithm, n, r, p, salt, expected = encoded.split("$")
            if marker != "higuma" or algorithm != "scrypt":
                return False
            work_factor, block_size, parallelism = int(n), int(r), int(p)
            if (
                work_factor < 2**10
                or work_factor > 2**20
                or work_factor & (work_factor - 1)
                or not 1 <= block_size <= 32
                or not 1 <= parallelism <= 16
            ):
                return False
            expected_bytes = _unb64(expected)
            salt_bytes = _unb64(salt)
            if not 8 <= len(salt_bytes) <= 64 or not 16 <= len(expected_bytes) <= 128:
                return False
            actual = hashlib.scrypt(
                password.encode("utf-8"),
                salt=salt_bytes,
                n=work_factor,
                r=block_size,
                p=parallelism,
                dklen=len(expected_bytes),
            )
        except (ValueError, TypeError, binascii.Error):
            return False
        return hmac.compare_digest(actual, expected_bytes)

    def needs_rehash(self, encoded: str) -> bool:
        try:
            marker, algorithm, n, r, p, salt, digest = encoded.split("$")
            return (
                marker != "higuma"
                or algorithm != "scrypt"
                or int(n) != self.n
                or int(r) != self.r
                or int(p) != self.p
                or len(_unb64(salt)) != self.salt_bytes
                or len(_unb64(digest)) != self.key_bytes
            )
        except (ValueError, TypeError, binascii.Error):
            return True


class TokenSigner:
    def __init__(self, secret_key: str | bytes, *, salt: str = "higuma-token") -> None:
        secret = _validate_secret_key(secret_key)
        self.key = hmac.new(secret, salt.encode(), hashlib.sha256).digest()

    def dumps(self, value: Any) -> str:
        payload = json.dumps(
            {"iat": int(time.time()), "value": value},
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode()
        encoded = _b64(payload)
        signature = _b64(hmac.new(self.key, encoded.encode(), hashlib.sha256).digest())
        return f"{encoded}.{signature}"

    def loads(self, token: str, *, max_age: int | None = None) -> Any:
        if len(token) > 64 * 1024:
            raise ValueError("token is too large")
        try:
            encoded, signature = token.rsplit(".", 1)
            expected = hmac.new(self.key, encoded.encode(), hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _unb64(signature)):
                raise ValueError("invalid token signature")
            payload = json.loads(_unb64(encoded))
            issued_at = int(payload["iat"])
            if issued_at > int(time.time()) + 60:
                raise ValueError("token timestamp is in the future")
            if max_age is not None and time.time() - issued_at > max_age:
                raise ValueError("token has expired")
            return payload["value"]
        except (KeyError, TypeError, ValueError, binascii.Error, json.JSONDecodeError) as exc:
            raise ValueError("invalid token") from exc


class CSRFProtection:
    def __init__(
        self,
        *,
        header_name: str = "x-csrf-token",
        field_name: str = "_csrf_token",
        safe_methods: tuple[str, ...] = ("GET", "HEAD", "OPTIONS"),
    ) -> None:
        if not header_name or not field_name:
            raise ValueError("header_name and field_name must not be empty")
        self.header_name = header_name
        self.field_name = field_name
        self.safe_methods = tuple(method.upper() for method in safe_methods)

    def __call__(
        self,
        current: Request,
        call_next: Callable[[Request], ResponseValue],
    ) -> ResponseValue:
        session = getattr(current, "session", None)
        if session is None:
            raise RuntimeError("CSRFProtection requires SessionMiddleware")
        current.state["_higuma_csrf_field_name"] = self.field_name
        expected = session.get(self.field_name)
        if expected is None:
            expected = secrets.token_urlsafe(32)
            session[self.field_name] = expected
        if current.method not in self.safe_methods:
            supplied = current.headers.get(self.header_name) or current.form.get(self.field_name)
            if not supplied or not hmac.compare_digest(str(expected), str(supplied)):
                raise Forbidden(detail="invalid or missing CSRF token")
        return call_next(current)


class RateLimitMiddleware:
    def __init__(
        self,
        limit: int = 100,
        window: float = 60.0,
        *,
        key: Callable[[Request], str] | None = None,
        max_keys: int = 10_000,
    ) -> None:
        if limit <= 0 or window <= 0 or max_keys <= 0:
            raise ValueError("limit, window, and max_keys must be positive")
        self.limit = limit
        self.window = window
        self.key = key or self._default_key
        self.max_keys = max_keys
        self._requests: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = Lock()
        self._last_cleanup = time.monotonic()

    def __call__(
        self,
        current: Request,
        call_next: Callable[[Request], ResponseValue],
    ) -> ResponseValue:
        now = time.monotonic()
        identity = self.key(current)
        with self._lock:
            if now - self._last_cleanup >= self.window:
                self._requests = OrderedDict(
                    (key, values)
                    for key, values in self._requests.items()
                    if values and values[-1] > now - self.window
                )
                self._last_cleanup = now
            bucket = self._requests.get(identity)
            if bucket is None:
                if len(self._requests) >= self.max_keys:
                    self._requests.popitem(last=False)
                bucket = deque()
                self._requests[identity] = bucket
            else:
                self._requests.move_to_end(identity)
            while bucket and bucket[0] <= now - self.window:
                bucket.popleft()
            if len(bucket) >= self.limit:
                retry_after = max(1, int(self.window - (now - bucket[0])))
                raise TooManyRequests(
                    detail="rate limit exceeded",
                    headers={"retry-after": str(retry_after)},
                )
            bucket.append(now)
            remaining = self.limit - len(bucket)
        response = call_next(current)
        from .response import make_response

        materialized = make_response(response)
        materialized.headers.setdefault("x-ratelimit-limit", str(self.limit))
        materialized.headers.setdefault("x-ratelimit-remaining", str(remaining))
        return materialized

    @staticmethod
    def _default_key(current: Request) -> str:
        return current.client_addr or "anonymous"


def generate_user_id(prefix: str = "usr", *, entropy_bytes: int = 18) -> str:
    if not prefix or not prefix.replace("_", "").isalnum():
        raise ValueError("prefix must contain only letters, numbers, and underscores")
    return f"{prefix}_{secrets.token_urlsafe(entropy_bytes)}"


def validate_user_id(value: str) -> bool:
    return bool(_USER_ID_RE.fullmatch(value))


def csrf_token(field_name: str | None = None) -> str:
    session = getattr(request, "session", None)
    if session is None:
        raise RuntimeError("csrf_token requires SessionMiddleware")
    resolved_field_name = (
        field_name
        if field_name is not None
        else request.state.get("_higuma_csrf_field_name", "_csrf_token")
    )
    if not resolved_field_name:
        raise ValueError("field_name must not be empty")
    token = session.get(resolved_field_name)
    if token is None:
        token = secrets.token_urlsafe(32)
        session[resolved_field_name] = token
    return str(token)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _unb64(value: str) -> bytes:
    return base64.b64decode(
        value + "=" * (-len(value) % 4),
        altchars=b"-_",
        validate=True,
    )


def _validate_secret_key(secret_key: str | bytes) -> bytes:
    secret = secret_key.encode("utf-8") if isinstance(secret_key, str) else bytes(secret_key)
    if len(secret) < 32:
        raise ValueError("secret_key must contain at least 32 bytes")
    return secret
