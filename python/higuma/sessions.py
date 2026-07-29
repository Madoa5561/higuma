from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable
from typing import Any

from .request import Request
from .response import Response, ResponseValue, make_response
from .security import _validate_secret_key


class Session(dict[str, Any]):
    def __init__(
        self,
        *args: Any,
        permanent: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.modified = False
        self._permanent = bool(permanent)

    @property
    def permanent(self) -> bool:
        return self._permanent

    @permanent.setter
    def permanent(self, value: bool) -> None:
        value = bool(value)
        if value != self._permanent:
            self.modified = True
            self._permanent = value

    def __setitem__(self, key: str, value: Any) -> None:
        self.modified = True
        super().__setitem__(key, value)

    def __delitem__(self, key: str) -> None:
        self.modified = True
        super().__delitem__(key)

    def clear(self) -> None:
        self.modified = True
        super().clear()

    def pop(self, key: str, default: Any = None) -> Any:
        self.modified = True
        return super().pop(key, default)

    def update(self, *args: Any, **kwargs: Any) -> None:
        self.modified = True
        super().update(*args, **kwargs)


class SessionMiddleware:
    def __init__(
        self,
        secret_key: str | bytes,
        *,
        cookie_name: str = "higuma_session",
        max_age: int = 14 * 24 * 60 * 60,
        secure: bool = False,
        httponly: bool = True,
        samesite: str = "Lax",
    ) -> None:
        self.secret_key = _validate_secret_key(secret_key)
        self.cookie_name = cookie_name
        self.max_age = max_age
        self.secure = secure
        self.httponly = httponly
        self.samesite = samesite

    def __call__(
        self,
        request: Request,
        call_next: Callable[[Request], ResponseValue],
    ) -> Response:
        session = self._load(request.cookies.get(self.cookie_name))
        request.session = session
        response = make_response(call_next(request))

        if session.modified:
            if session:
                response.set_cookie(
                    self.cookie_name,
                    self._dump(session),
                    max_age=self.max_age if session.permanent else None,
                    secure=self.secure,
                    httponly=self.httponly,
                    samesite=self.samesite,
                )
            else:
                response.delete_cookie(self.cookie_name)
        return response

    def _dump(self, session: Session) -> str:
        payload = json.dumps(
            {
                "data": dict(session),
                "iat": int(time.time()),
                "permanent": session.permanent,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
        signature = hmac.new(self.secret_key, encoded, hashlib.sha256).digest()
        encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=")
        value = f"{encoded.decode()}.{encoded_signature.decode()}"
        if len(value) > 4093:
            raise ValueError("session cookie exceeds the 4093-byte safety limit")
        return value

    def _load(self, value: str | None) -> Session:
        if not value or len(value) > 16 * 1024 or "." not in value:
            return Session()
        encoded, encoded_signature = value.rsplit(".", 1)
        try:
            expected = hmac.new(
                self.secret_key,
                encoded.encode("ascii"),
                hashlib.sha256,
            ).digest()
            actual = _decode_base64(encoded_signature)
        except (ValueError, UnicodeEncodeError):
            return Session()
        if not hmac.compare_digest(expected, actual):
            return Session()
        try:
            payload = json.loads(_decode_base64(encoded).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return Session()
        if not isinstance(payload, dict):
            return Session()
        issued_at = payload.get("iat")
        data = payload.get("data")
        permanent = payload.get("permanent", False)
        if not isinstance(issued_at, int) or not isinstance(data, dict):
            return Session()
        if not isinstance(permanent, bool):
            return Session()
        if issued_at > int(time.time()) + 60 or time.time() - issued_at > self.max_age:
            return Session()
        return Session(data, permanent=permanent)


def _decode_base64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
