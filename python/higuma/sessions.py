from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Callable
from typing import Any

from .request import Request
from .response import Response, ResponseValue, make_response


class Session(dict[str, Any]):
    modified = False

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
        if not secret_key:
            raise ValueError("secret_key must not be empty")
        self.secret_key = (
            secret_key.encode("utf-8") if isinstance(secret_key, str) else secret_key
        )
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
                    max_age=self.max_age,
                    secure=self.secure,
                    httponly=self.httponly,
                    samesite=self.samesite,
                )
            else:
                response.delete_cookie(self.cookie_name)
        return response

    def _dump(self, session: Session) -> str:
        payload = json.dumps(
            dict(session),
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
        signature = hmac.new(self.secret_key, encoded, hashlib.sha256).digest()
        encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=")
        return f"{encoded.decode()}.{encoded_signature.decode()}"

    def _load(self, value: str | None) -> Session:
        if not value or "." not in value:
            return Session()
        encoded, encoded_signature = value.rsplit(".", 1)
        expected = hmac.new(
            self.secret_key,
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
        try:
            actual = _decode_base64(encoded_signature)
        except (ValueError, UnicodeEncodeError):
            return Session()
        if not hmac.compare_digest(expected, actual):
            return Session()
        try:
            data = json.loads(_decode_base64(encoded).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return Session()
        return Session(data) if isinstance(data, dict) else Session()


def _decode_base64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
