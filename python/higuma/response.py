from __future__ import annotations

import json
import mimetypes
from collections.abc import Mapping
from datetime import datetime, timezone
from email.utils import format_datetime
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import quote


class Response:
    __higuma_response__ = True

    def __init__(
        self,
        body: str | bytes | bytearray | memoryview | None = b"",
        status: int = 200,
        headers: Mapping[str, str] | None = None,
        media_type: str | None = None,
    ) -> None:
        if body is None:
            body = b""
        if isinstance(body, str):
            body = body.encode("utf-8")
            media_type = media_type or "text/html; charset=utf-8"
        elif not isinstance(body, bytes):
            body = bytes(body)

        self.body = body
        self.status_code = int(status)
        self.headers = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
        self._extra_headers: list[tuple[str, str]] = []
        self.media_type = media_type

    @property
    def status(self) -> int:
        return self.status_code

    @status.setter
    def status(self, value: int) -> None:
        self.status_code = int(value)

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def get_json(self) -> Any:
        return json.loads(self.text)

    @property
    def json(self) -> Any:
        return self.get_json()

    @property
    def header_items(self) -> list[tuple[str, str]]:
        return [*self.headers.items(), *self._extra_headers]

    def set_cookie(
        self,
        key: str,
        value: str = "",
        *,
        max_age: int | None = None,
        expires: datetime | str | None = None,
        path: str = "/",
        domain: str | None = None,
        secure: bool = False,
        httponly: bool = False,
        samesite: str | None = "Lax",
    ) -> None:
        cookie = SimpleCookie()
        cookie[key] = value
        morsel = cookie[key]
        if max_age is not None:
            morsel["max-age"] = str(max_age)
        if isinstance(expires, datetime):
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            morsel["expires"] = format_datetime(expires, usegmt=True)
        elif expires is not None:
            morsel["expires"] = expires
        morsel["path"] = path
        if domain:
            morsel["domain"] = domain
        if secure:
            morsel["secure"] = True
        if httponly:
            morsel["httponly"] = True
        if samesite:
            morsel["samesite"] = samesite
        self._extra_headers.append(("set-cookie", morsel.OutputString()))

    def delete_cookie(self, key: str, *, path: str = "/", domain: str | None = None) -> None:
        self.set_cookie(
            key,
            "",
            max_age=0,
            expires=datetime(1970, 1, 1, tzinfo=timezone.utc),
            path=path,
            domain=domain,
        )


class HTMLResponse(Response):
    def __init__(
        self,
        body: str | bytes,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(body, status, headers, "text/html; charset=utf-8")


class PlainTextResponse(Response):
    def __init__(
        self,
        body: str | bytes,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(body, status, headers, "text/plain; charset=utf-8")


class JSONResponse(Response):
    def __init__(
        self,
        data: Any,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        body = json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        super().__init__(body, status, headers, "application/json; charset=utf-8")


class RedirectResponse(Response):
    def __init__(
        self,
        location: str,
        status: int = 302,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        response_headers = dict(headers or {})
        response_headers["location"] = location
        super().__init__(b"", status, response_headers, "text/plain; charset=utf-8")


class FileResponse(Response):
    __higuma_file__ = True

    def __init__(
        self,
        path: str | Path,
        *,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
        media_type: str | None = None,
        filename: str | None = None,
        as_attachment: bool = False,
    ) -> None:
        file_path = Path(path).resolve()
        resolved_media_type = (
            media_type
            or mimetypes.guess_type(file_path.name)[0]
            or "application/octet-stream"
        )
        super().__init__(b"", status, headers, resolved_media_type)
        self.path = str(file_path)

        download_name = filename or file_path.name
        disposition = "attachment" if as_attachment else "inline"
        encoded_name = quote(download_name)
        self.headers.setdefault(
            "content-disposition",
            f"{disposition}; filename*=UTF-8''{encoded_name}",
        )
        try:
            stat = file_path.stat()
        except OSError:
            return
        self.headers.setdefault("content-length", str(stat.st_size))
        modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        self.headers.setdefault("last-modified", format_datetime(modified, usegmt=True))


class TemplateResponse(Response):
    __higuma_template__ = True

    def __init__(
        self,
        template: str,
        context: dict[str, Any] | None = None,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(b"", status, headers, "text/html; charset=utf-8")
        self.template = template
        self.context = context or {}

    @property
    def context_json(self) -> str:
        return json.dumps(self.context, ensure_ascii=False, default=str)


ResponseValue = (
    Response
    | FileResponse
    | TemplateResponse
    | str
    | bytes
    | bytearray
    | memoryview
    | dict[str, Any]
    | list[Any]
    | tuple[Any, ...]
    | None
)


def make_response(
    value: ResponseValue,
    status: int | None = None,
    headers: Mapping[str, str] | None = None,
) -> Response | FileResponse | TemplateResponse:
    tuple_headers: Mapping[str, str] | None = None
    if isinstance(value, tuple):
        if len(value) == 2:
            value, tuple_status = value
            status = int(tuple_status)
        elif len(value) == 3:
            value, tuple_status, tuple_headers = value
            status = int(tuple_status)
        else:
            raise TypeError(
                "response tuple must be (body, status) or (body, status, headers)"
            )

    if isinstance(value, (Response, FileResponse, TemplateResponse)):
        response = value
        if status is not None:
            if isinstance(response, TemplateResponse):
                response.status = status
            else:
                response.status_code = status
        merged_headers = {**dict(tuple_headers or {}), **dict(headers or {})}
        response.headers.update(
            {str(key).lower(): str(header_value) for key, header_value in merged_headers.items()}
        )
        return response

    final_status = status or 200
    final_headers = {**dict(tuple_headers or {}), **dict(headers or {})}
    if isinstance(value, (dict, list)):
        return JSONResponse(value, final_status, final_headers)
    if value is None:
        return Response(b"", final_status, final_headers)
    if isinstance(value, str):
        return HTMLResponse(value, final_status, final_headers)
    return Response(value, final_status, final_headers)


def jsonify(data: Any = None, /, **kwargs: Any) -> JSONResponse:
    if data is not None and kwargs:
        raise TypeError("jsonify accepts either one positional value or keyword fields")
    return JSONResponse(kwargs if data is None else data)


def redirect(location: str, status: int = 302) -> RedirectResponse:
    return RedirectResponse(location, status)


def render_template(template: str, /, **context: Any) -> TemplateResponse:
    return TemplateResponse(template=template, context=context)


def send_file(
    path: str | Path,
    *,
    as_attachment: bool = False,
    download_name: str | None = None,
    media_type: str | None = None,
) -> FileResponse:
    return FileResponse(
        path,
        as_attachment=as_attachment,
        filename=download_name,
        media_type=media_type,
    )
