from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextvars import ContextVar, Token
from http.cookies import SimpleCookie
from types import MappingProxyType
from typing import Any, Generic, TypeVar
from urllib.parse import parse_qsl

from .exceptions import BadRequest, UnsupportedMediaType

T = TypeVar("T")


class MultiDict(Mapping[str, str]):
    def __init__(self, pairs: Iterator[tuple[str, str]] | list[tuple[str, str]] = ()) -> None:
        self._values: dict[str, list[str]] = {}
        for key, value in pairs:
            self._values.setdefault(key, []).append(value)

    def __getitem__(self, key: str) -> str:
        return self._values[key][-1]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def get(
        self,
        key: str,
        default: T | None = None,
        type: type[T] | None = None,
    ) -> str | T | None:
        if key not in self._values:
            return default
        value = self._values[key][-1]
        if type is None:
            return value
        try:
            return type(value)
        except (TypeError, ValueError):
            return default

    def getlist(self, key: str, type: type[T] | None = None) -> list[str] | list[T]:
        values = list(self._values.get(key, ()))
        if type is None:
            return values
        converted: list[T] = []
        for value in values:
            try:
                converted.append(type(value))
            except (TypeError, ValueError):
                continue
        return converted

    def items(self, multi: bool = False):
        if multi:
            return (
                (key, value)
                for key, values in self._values.items()
                for value in values
            )
        return ((key, values[-1]) for key, values in self._values.items())

    def to_dict(self, flat: bool = True) -> dict[str, Any]:
        if flat:
            return {key: values[-1] for key, values in self._values.items()}
        return {key: list(values) for key, values in self._values.items()}


class Headers(Mapping[str, str]):
    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        self._values = {str(key).lower(): str(value) for key, value in (values or {}).items()}

    def __getitem__(self, key: str) -> str:
        return self._values[key.lower()]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def get(self, key: str, default: T | None = None) -> str | T | None:
        return self._values.get(key.lower(), default)


class Request:
    def __init__(self, raw: Mapping[str, Any]) -> None:
        self.method = str(raw.get("method", "GET")).upper()
        self.path = str(raw.get("path", "/"))
        self.query_string = str(raw.get("query_string", ""))
        self.args = MultiDict(iter(parse_qsl(self.query_string, keep_blank_values=True)))
        self.query = self.args
        self.headers = Headers(raw.get("headers", {}))
        self.body = bytes(raw.get("body", b""))
        self.path_params = dict(raw.get("path_params", {}))
        self.view_args = self.path_params
        self.route_pattern = raw.get("route_pattern")
        self.route_error_status = int(raw.get("route_error_status", 404))
        self.allowed_methods = tuple(raw.get("allowed_methods", ()))
        self.state: dict[str, Any] = {}
        self._json_cache: Any = _MISSING
        self._form_cache: MultiDict | None = None
        self._cookies_cache: Mapping[str, str] | None = None

    def __getitem__(self, key: str) -> Any:
        aliases = {
            "method": self.method,
            "path": self.path,
            "query": self.args.to_dict(),
            "query_string": self.query_string,
            "headers": self.headers,
            "body": self.body,
            "text": self.text,
            "path_params": self.path_params,
        }
        try:
            return aliases[key]
        except KeyError as exc:
            raise KeyError(key) from exc

    @property
    def content_type(self) -> str | None:
        value = self.headers.get("content-type")
        return str(value) if value is not None else None

    @property
    def content_length(self) -> int | None:
        value = self.headers.get("content-length")
        if value is None:
            return len(self.body)
        try:
            return int(value)
        except ValueError:
            return None

    @property
    def is_json(self) -> bool:
        content_type = (self.content_type or "").split(";", 1)[0].strip().lower()
        return content_type == "application/json" or content_type.endswith("+json")

    @property
    def text(self) -> str:
        charset = "utf-8"
        content_type = self.content_type or ""
        for item in content_type.split(";")[1:]:
            key, _, value = item.strip().partition("=")
            if key.lower() == "charset" and value:
                charset = value.strip("\"'")
        return self.body.decode(charset, errors="replace")

    @property
    def json(self) -> Any:
        return self.get_json()

    def get_json(self, force: bool = False, silent: bool = False) -> Any:
        if self._json_cache is not _MISSING:
            return self._json_cache
        if not force and not self.is_json:
            if silent:
                return None
            raise UnsupportedMediaType(detail="request Content-Type must be application/json")
        try:
            self._json_cache = json.loads(self.text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            if silent:
                return None
            raise BadRequest(detail="invalid JSON request body") from exc
        return self._json_cache

    @property
    def form(self) -> MultiDict:
        if self._form_cache is None:
            content_type = (self.content_type or "").split(";", 1)[0].strip().lower()
            if content_type == "application/x-www-form-urlencoded":
                self._form_cache = MultiDict(
                    iter(parse_qsl(self.text, keep_blank_values=True))
                )
            else:
                self._form_cache = MultiDict()
        return self._form_cache

    @property
    def cookies(self) -> Mapping[str, str]:
        if self._cookies_cache is None:
            cookie = SimpleCookie()
            cookie.load(str(self.headers.get("cookie", "")))
            self._cookies_cache = MappingProxyType(
                {key: morsel.value for key, morsel in cookie.items()}
            )
        return self._cookies_cache

    def get_data(self, as_text: bool = False) -> bytes | str:
        return self.text if as_text else self.body


_MISSING = object()
_request_context: ContextVar[Request | None] = ContextVar(
    "higuma_request", default=None
)


def _push_request(request: Request) -> Token[Request | None]:
    return _request_context.set(request)


def _pop_request(token: Token[Request | None]) -> None:
    _request_context.reset(token)


class LocalProxy(Generic[T]):
    def _get_current(self) -> T:
        value = _request_context.get()
        if value is None:
            raise RuntimeError("working outside of a request context")
        return value  # type: ignore[return-value]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get_current(), name)

    def __getitem__(self, key: str) -> Any:
        return self._get_current()[key]  # type: ignore[index]

    def __repr__(self) -> str:
        try:
            return repr(self._get_current())
        except RuntimeError:
            return "<LocalProxy unbound>"


request: LocalProxy[Request] = LocalProxy()
