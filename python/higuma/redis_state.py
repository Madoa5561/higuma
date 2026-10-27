"""Opt-in synchronous Redis middleware. Fail closed; never flush shared databases."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets

from .exceptions import Conflict, HTTPException, TooManyRequests
from .response import make_response
from .sessions import Session, SessionMiddleware

_SID = re.compile(r"[A-Za-z0-9_-]{43}")
_PREFIX = re.compile(r"[A-Za-z0-9:_-]{1,127}:")
_SAVE = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
return 1
"""
_LIMIT = """
local count = tonumber(redis.call('GET', KEYS[1]) or '0')
if count >= tonumber(ARGV[1]) then return {0, count, redis.call('PTTL', KEYS[1])} end
count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('PEXPIRE', KEYS[1], ARGV[2]) end
return {1, count, redis.call('PTTL', KEYS[1])}
"""


def _prefix(value):
    if not isinstance(value, str) or not _PREFIX.fullmatch(value):
        raise ValueError("use a unique application prefix ending with ':' (letters/digits/:/_/-)")
    return value


def _operation(client, method, *args, **kwargs):
    from redis.exceptions import RedisError

    try:
        connection = client() if callable(client) else client
        result = getattr(connection, method)(*args, **kwargs)
        import inspect

        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise TypeError("Redis middleware requires a synchronous Redis client")
        return result
    except RedisError as error:
        raise HTTPException(503, "State service unavailable") from error


class RedisSessionStore:
    def __init__(
        self, client, *, prefix: str, ttl: int = 14 * 24 * 60 * 60, max_bytes: int = 65536
    ):
        if not isinstance(ttl, int) or ttl < 1 or not isinstance(max_bytes, int) or max_bytes < 1:
            raise ValueError("ttl and max_bytes must be positive integers")
        self.client, self.prefix, self.ttl, self.max_bytes = client, _prefix(prefix), ttl, max_bytes

    def _key(self, sid):
        if not isinstance(sid, str) or not _SID.fullmatch(sid):
            raise ValueError("invalid session identifier")
        return self.prefix + "session:" + sid

    def load(self, sid):
        return _operation(self.client, "get", self._key(sid))

    def create(self, payload):
        for _ in range(3):
            sid = secrets.token_urlsafe(32)
            if _operation(self.client, "set", self._key(sid), payload, nx=True, ex=self.ttl):
                return sid
        raise RuntimeError("could not allocate a session identifier")

    def save(self, sid, expected, payload):
        if not _operation(
            self.client, "eval", _SAVE, 1, self._key(sid), expected, payload, self.ttl
        ):
            raise Conflict(detail="Session changed or was revoked; reload before retrying")

    def revoke(self, sid):
        return bool(_operation(self.client, "delete", self._key(sid)))


class ServerSession(Session):
    def __init__(self, *args, sid=None, stored=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.sid, self.stored, self.rotated = sid, stored, False

    def clear(self):
        super().clear()
        self.rotated = True
        self.modified = True


class RedisSessionMiddleware(SessionMiddleware):
    def __init__(self, secret_key, *, store: RedisSessionStore, **options):
        if "max_age" in options and options["max_age"] != store.ttl:
            raise ValueError("Cookie max_age must match the Redis store TTL")
        super().__init__(
            secret_key,
            max_age=store.ttl,
            **{key: value for key, value in options.items() if key != "max_age"},
        )
        self.store = store

    def _signature(self, sid):
        return hmac.new(self.secret_key, sid.encode("ascii"), hashlib.sha256).hexdigest()

    def _load(self, value):
        if not value or len(value) != 108:
            return ServerSession()
        sid, separator, signature = value.partition(".")
        if (
            not separator
            or not _SID.fullmatch(sid)
            or not re.fullmatch(r"[0-9a-f]{64}", signature)
            or not hmac.compare_digest(signature, self._signature(sid))
        ):
            return ServerSession()
        payload = self.store.load(sid)
        if payload is None or len(payload) > self.store.max_bytes:
            return ServerSession()
        try:
            document = json.loads(payload)
            if not isinstance(document["data"], dict) or not isinstance(
                document["permanent"], bool
            ):
                return ServerSession()
            return ServerSession(
                document["data"], permanent=document["permanent"], sid=sid, stored=payload
            )
        except (ValueError, KeyError, TypeError, UnicodeError):
            return ServerSession()

    def _save(self, response, session):
        response.headers["vary"] = ", ".join(
            dict.fromkeys(
                [
                    *[
                        value.strip()
                        for value in response.headers.get("vary", "").split(",")
                        if value.strip()
                    ],
                    "Cookie",
                ]
            )
        )
        if not session.modified:
            return response
        payload = json.dumps(
            {
                "data": dict(session),
                "permanent": session.permanent,
                "revision": secrets.token_hex(16),
            },
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        if len(payload) > self.store.max_bytes:
            raise ValueError("server session exceeds the configured size limit")
        sid = session.sid
        if session.rotated and sid:
            self.store.revoke(sid)
            sid = None
        if not session:
            if sid:
                self.store.revoke(sid)
            response.delete_cookie(
                self.cookie_name, secure=self.secure, httponly=self.httponly, samesite=self.samesite
            )
            session.sid = None
            return response
        if sid:
            self.store.save(sid, session.stored, payload)
        else:
            sid = self.store.create(payload)
        session.sid, session.stored, session.modified, session.rotated = sid, payload, False, False
        response.set_cookie(
            self.cookie_name,
            sid + "." + self._signature(sid),
            max_age=self.max_age if session.permanent else None,
            secure=self.secure,
            httponly=self.httponly,
            samesite=self.samesite,
        )
        return response


class RedisRateLimitMiddleware:
    """Atomic fixed windows shared across processes; Redis TIME/TTL owns the clock."""

    def __init__(self, client, *, prefix: str, limit: int = 100, window: float = 60, key=None):
        if not isinstance(limit, int) or limit < 1 or not math.isfinite(window) or window <= 0:
            raise ValueError("limit and window must be positive and finite")
        self.client, self.prefix, self.limit = client, _prefix(prefix), limit
        self.window_ms = max(1, math.ceil(window * 1000))
        self.key = key or (lambda request: request.client_addr or "anonymous")

    def __call__(self, request, call_next):
        identity = str(self.key(request)).encode()
        key = self.prefix + "limit:" + hashlib.sha256(identity).hexdigest()
        allowed, count, ttl = _operation(
            self.client, "eval", _LIMIT, 1, key, self.limit, self.window_ms
        )
        if not allowed:
            raise TooManyRequests(headers={"retry-after": str(max(1, math.ceil(ttl / 1000)))})
        response = make_response(call_next(request))
        response.headers.setdefault("x-ratelimit-limit", str(self.limit))
        response.headers.setdefault("x-ratelimit-remaining", str(max(0, self.limit - count)))
        return response
