from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import sys
from collections.abc import AsyncIterable, Callable, Iterable, Mapping
from contextvars import copy_context
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from http.cookies import SimpleCookie
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.parse import quote

from .background import BackgroundTask, BackgroundTasks
from .parameters import serialize_json_value

_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


def _validate_status(status: int) -> int:
    value = int(status)
    if not 200 <= value <= 599:
        raise ValueError("final HTTP status code must be between 200 and 599")
    return value


def _validate_header(name: str, value: str) -> tuple[str, str]:
    normalized = str(name).lower()
    text = str(value)
    if not _HEADER_NAME_RE.fullmatch(normalized):
        raise ValueError(f"invalid HTTP header name: {name!r}")
    if "\r" in text or "\n" in text or "\x00" in text:
        raise ValueError(f"invalid HTTP header value for {normalized!r}")
    return normalized, text


class _ResponseHeaders(dict[str, str]):
    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        super().__init__()
        self.update(values or {})

    def __setitem__(self, name: str, value: str) -> None:
        normalized, text = _validate_header(name, value)
        super().__setitem__(normalized, text)

    def setdefault(self, name: str, value: str = "") -> str:
        normalized, text = _validate_header(name, value)
        return super().setdefault(normalized, text)

    def update(
        self,
        values: Mapping[str, str] | None = None,
        **kwargs: str,
    ) -> None:
        pairs = dict(values or {}, **kwargs)
        validated = [_validate_header(name, value) for name, value in pairs.items()]
        for name, value in validated:
            super().__setitem__(name, value)


class Response:
    __higuma_response__ = True

    def __init__(
        self,
        body: str | bytes | bytearray | memoryview | None = b"",
        status: int = 200,
        headers: Mapping[str, str] | None = None,
        media_type: str | None = None,
        background: BackgroundTask | BackgroundTasks | Callable[[], Any] | None = None,
    ) -> None:
        if body is None:
            body = b""
        if isinstance(body, str):
            body = body.encode("utf-8")
            media_type = media_type or "text/html; charset=utf-8"
        elif not isinstance(body, bytes):
            body = bytes(body)

        self.body = body
        self.status_code = _validate_status(status)
        self.headers = _ResponseHeaders(headers)
        self._extra_headers: list[tuple[str, str]] = []
        self.media_type = media_type
        self.history: tuple[Response, ...] = ()
        if background is not None and not callable(background):
            raise TypeError("background must be callable")
        self.background = background

    @property
    def status_code(self) -> int:
        return self._status_code

    @status_code.setter
    def status_code(self, value: int) -> None:
        self._status_code = _validate_status(value)

    @property
    def status(self) -> int:
        return self.status_code

    @status.setter
    def status(self, value: int) -> None:
        self.status_code = value

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

    def append_header(self, name: str, value: str) -> None:
        self._extra_headers.append(_validate_header(name, value))

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
        partitioned: bool = False,
    ) -> None:
        if samesite is not None and samesite.lower() not in {"lax", "strict", "none"}:
            raise ValueError("samesite must be 'Lax', 'Strict', 'None', or None")
        if partitioned and sys.version_info < (3, 14):
            raise ValueError("partitioned cookies require Python 3.14 or newer")
        if partitioned and not secure:
            raise ValueError("partitioned cookies must also set secure=True")
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
        if partitioned:
            morsel["partitioned"] = True
        self.append_header("set-cookie", morsel.OutputString())

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
        background: BackgroundTask | BackgroundTasks | Callable[[], Any] | None = None,
    ) -> None:
        super().__init__(body, status, headers, "text/html; charset=utf-8", background)


class PlainTextResponse(Response):
    def __init__(
        self,
        body: str | bytes,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
        background: BackgroundTask | BackgroundTasks | Callable[[], Any] | None = None,
    ) -> None:
        super().__init__(body, status, headers, "text/plain; charset=utf-8", background)


class JSONResponse(Response):
    def __init__(
        self,
        data: Any,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
        background: BackgroundTask | BackgroundTasks | Callable[[], Any] | None = None,
    ) -> None:
        body = json.dumps(
            serialize_json_value(data),
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        super().__init__(body, status, headers, "application/json; charset=utf-8", background)


class RedirectResponse(Response):
    def __init__(
        self,
        location: str,
        status: int = 302,
        headers: Mapping[str, str] | None = None,
        background: BackgroundTask | BackgroundTasks | Callable[[], Any] | None = None,
    ) -> None:
        response_headers = dict(headers or {})
        response_headers["location"] = location
        super().__init__(b"", status, response_headers, "text/plain; charset=utf-8", background)


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
        offset: int = 0,
        length: int | None = None,
        background: BackgroundTask | BackgroundTasks | Callable[[], Any] | None = None,
    ) -> None:
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        if length is not None and (
            not isinstance(length, int) or isinstance(length, bool) or length < 0
        ):
            raise ValueError("length must be a non-negative integer or None")
        file_path = Path(path).resolve()
        resolved_media_type = (
            media_type or mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        )
        super().__init__(b"", status, headers, resolved_media_type, background)
        self.path = str(file_path)
        self.offset = offset
        self.length = length

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
        if offset > stat.st_size:
            raise ValueError("offset exceeds the response file size")
        selected_length = stat.st_size - offset if length is None else length
        if selected_length > stat.st_size - offset:
            raise ValueError("length exceeds the available response file range")
        self.length = selected_length
        self.headers.setdefault("content-length", str(selected_length))
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
        background: BackgroundTask | BackgroundTasks | Callable[[], Any] | None = None,
    ) -> None:
        super().__init__(b"", status, headers, "text/html; charset=utf-8", background)
        self.template = template
        self.context = context or {}

    @property
    def context_json(self) -> str:
        return json.dumps(self.context, ensure_ascii=False, default=str)


class StreamingResponse(Response):
    __higuma_stream__ = True

    def __init__(
        self,
        content: Iterable[str | bytes] | AsyncIterable[str | bytes],
        *,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
        media_type: str = "application/octet-stream",
        content_length: int | None = None,
        background: BackgroundTask | BackgroundTasks | Callable[[], Any] | None = None,
    ) -> None:
        if content_length is not None and (
            not isinstance(content_length, int)
            or isinstance(content_length, bool)
            or content_length < 0
        ):
            raise ValueError("content_length must be a non-negative integer or None")
        response_headers = dict(headers or {})
        if content_length is None:
            response_headers.pop("content-length", None)
            response_headers.pop("Content-Length", None)
        else:
            response_headers["content-length"] = str(content_length)
        super().__init__(b"", status, response_headers, media_type, background)
        if isinstance(content, AsyncIterable):
            self._iterator = content.__aiter__()
            self._is_async = True
        elif isinstance(content, Iterable):
            self._iterator = iter(content)
            self._is_async = False
        else:
            raise TypeError("streaming content must be an iterable or async iterable")
        self._context = copy_context()
        self._finished = False

    def _next_chunk(self) -> bytes | None:
        return self._context.run(self._next_chunk_in_context)

    def _next_chunk_in_context(self) -> bytes | None:
        if self._finished:
            return None
        try:
            if self._is_async:
                chunk = _resolve_stream_awaitable(self._iterator.__anext__())
            else:
                chunk = next(self._iterator)
        except (StopIteration, StopAsyncIteration):
            self._finished = True
            return None
        return self._encode_chunk(chunk)

    def _encode_chunk(self, chunk: Any) -> bytes:
        if isinstance(chunk, str):
            return chunk.encode("utf-8")
        if isinstance(chunk, bytes):
            return chunk
        if isinstance(chunk, (bytearray, memoryview)):
            return bytes(chunk)
        raise TypeError("stream chunks must be str or bytes-like")


@dataclass(frozen=True, slots=True)
class ServerSentEvent:
    data: Any = ""
    event: str | None = None
    id: str | None = None
    retry: int | None = None
    comment: str | None = None

    def encode(self) -> bytes:
        lines: list[str] = []
        if self.comment is not None:
            lines.extend(f": {line}" for line in str(self.comment).splitlines() or [""])
        if self.event is not None:
            lines.append(f"event: {_sse_single_line(self.event, 'event')}")
        if self.id is not None:
            lines.append(f"id: {_sse_single_line(self.id, 'id')}")
        if self.retry is not None:
            if not isinstance(self.retry, int) or isinstance(self.retry, bool) or self.retry < 0:
                raise ValueError("SSE retry must be a non-negative integer")
            lines.append(f"retry: {self.retry}")
        data = (
            self.data
            if isinstance(self.data, str)
            else json.dumps(
                serialize_json_value(self.data),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        lines.extend(f"data: {line}" for line in data.splitlines() or [""])
        return ("\n".join(lines) + "\n\n").encode("utf-8")


class EventSourceResponse(StreamingResponse):
    def __init__(
        self,
        content: Iterable[Any] | AsyncIterable[Any],
        *,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
        background: BackgroundTask | BackgroundTasks | Callable[[], Any] | None = None,
    ) -> None:
        response_headers = {
            "cache-control": "no-cache",
            "x-accel-buffering": "no",
            **dict(headers or {}),
        }
        super().__init__(
            content,
            status=status,
            headers=response_headers,
            media_type="text/event-stream; charset=utf-8",
            background=background,
        )

    def _encode_chunk(self, chunk: Any) -> bytes:
        event = chunk if isinstance(chunk, ServerSentEvent) else ServerSentEvent(data=chunk)
        return event.encode()


def _sse_single_line(value: Any, field: str) -> str:
    text = str(value)
    if "\r" in text or "\n" in text or "\x00" in text:
        raise ValueError(f"SSE {field} must not contain line breaks or NUL")
    return text


def _resolve_stream_awaitable(value: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)

    result: list[Any] = []
    errors: list[BaseException] = []

    def runner() -> None:
        try:
            result.append(asyncio.run(value))
        except BaseException as exc:  # noqa: BLE001 - propagate iterator failure
            errors.append(exc)

    thread = Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    return result[0]


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
            raise TypeError("response tuple must be (body, status) or (body, status, headers)")

    if isinstance(value, (Response, FileResponse, TemplateResponse)):
        response = value
        if status is not None:
            response.status = status
        merged_headers = {**dict(tuple_headers or {}), **dict(headers or {})}
        response.headers.update(
            {str(key).lower(): str(header_value) for key, header_value in merged_headers.items()}
        )
        return response

    final_status = 200 if status is None else status
    final_headers = {**dict(tuple_headers or {}), **dict(headers or {})}
    if isinstance(value, (dict, list)) or (
        hasattr(value, "__dataclass_fields__") and not isinstance(value, type)
    ):
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
