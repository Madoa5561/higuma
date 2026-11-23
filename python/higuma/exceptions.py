from __future__ import annotations

import re
from collections.abc import Mapping
from http import HTTPStatus

_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


def _validate_exception_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    if headers is None:
        return {}
    if not isinstance(headers, Mapping):
        raise TypeError("exception headers must be a mapping")
    validated: dict[str, str] = {}
    for key, value in headers.items():
        name = str(key)
        text = str(value)
        if not _HEADER_NAME_RE.fullmatch(name):
            raise ValueError(f"invalid HTTP header name: {key!r}")
        if "\r" in text or "\n" in text or "\x00" in text:
            raise ValueError(f"invalid HTTP header value for {name!r}")
        validated[name] = text
    return validated


class HTTPException(Exception):
    status_code = 500

    def __init__(
        self,
        status_code: int | None = None,
        detail: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        if status_code is not None and (
            isinstance(status_code, bool) or not isinstance(status_code, int)
        ):
            raise ValueError("final HTTP status code must be an integer between 200 and 599")
        resolved_status = self.status_code if status_code is None else int(status_code)
        if not 200 <= resolved_status <= 599:
            raise ValueError("final HTTP status code must be between 200 and 599")
        self.status_code = resolved_status
        try:
            default_detail = HTTPStatus(self.status_code).phrase
        except ValueError:
            default_detail = "HTTP Error"
        self.detail = default_detail if detail is None else str(detail)
        self.headers = _validate_exception_headers(headers)
        super().__init__(self.detail)


class BadRequest(HTTPException):
    status_code = 400


class Unauthorized(HTTPException):
    status_code = 401


class Forbidden(HTTPException):
    status_code = 403


class NotFound(HTTPException):
    status_code = 404


class MethodNotAllowed(HTTPException):
    status_code = 405


class Conflict(HTTPException):
    status_code = 409


class RequestEntityTooLarge(HTTPException):
    status_code = 413


class UnsupportedMediaType(HTTPException):
    status_code = 415


class RangeNotSatisfiable(HTTPException):
    status_code = 416


class TooManyRequests(HTTPException):
    status_code = 429


class InternalServerError(HTTPException):
    status_code = 500


_STATUS_EXCEPTIONS = {
    cls.status_code: cls
    for cls in (
        BadRequest,
        Unauthorized,
        Forbidden,
        NotFound,
        MethodNotAllowed,
        Conflict,
        RequestEntityTooLarge,
        UnsupportedMediaType,
        RangeNotSatisfiable,
        TooManyRequests,
        InternalServerError,
    )
}


def _validate_error_handler_key(
    code_or_exception: int | type[BaseException],
) -> int | type[BaseException]:
    if isinstance(code_or_exception, bool):
        raise TypeError("error handler key must be an HTTP status code or exception class")
    if isinstance(code_or_exception, int):
        if not 200 <= code_or_exception <= 599:
            raise ValueError("HTTP error status code must be between 200 and 599")
        return int(code_or_exception)
    if isinstance(code_or_exception, type) and issubclass(code_or_exception, BaseException):
        return code_or_exception
    raise TypeError("error handler key must be an HTTP status code or exception class")


def abort(
    status_code: int,
    detail: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> None:
    if isinstance(status_code, bool) or not isinstance(status_code, int):
        raise ValueError("final HTTP status code must be an integer between 200 and 599")  # noqa: TRY004
    code = int(status_code)
    exception_type = _STATUS_EXCEPTIONS.get(code, HTTPException)
    raise exception_type(status_code=code, detail=detail, headers=headers)
