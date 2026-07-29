from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus


class HTTPException(Exception):
    status_code = 500

    def __init__(
        self,
        status_code: int | None = None,
        detail: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.status_code = status_code or self.status_code
        try:
            default_detail = HTTPStatus(self.status_code).phrase
        except ValueError:
            default_detail = "HTTP Error"
        self.detail = detail or default_detail
        self.headers = dict(headers or {})
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


def abort(
    status_code: int,
    detail: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> None:
    exception_type = _STATUS_EXCEPTIONS.get(status_code, HTTPException)
    raise exception_type(status_code=status_code, detail=detail, headers=headers)
