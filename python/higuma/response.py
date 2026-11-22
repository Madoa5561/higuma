from __future__ import annotations

import inspect
import json
import mimetypes
import re
import sys
from collections.abc import AsyncIterable, Callable, Iterable, Mapping
from contextvars import copy_context
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .background import BackgroundTask, BackgroundTasks
from .execution import resolve_awaitable
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

    def __getitem__(self, name: str) -> str:
        return super().__getitem__(name.lower())

    def get(self, name: str, default: Any = None) -> Any:
        return super().get(name.lower(), default)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and super().__contains__(name.lower())

    def __delitem__(self, name: str) -> None:
        super().__delitem__(name.lower())

    def pop(self, name: str, *default: Any) -> Any:
        return super().pop(name.lower(), *default)

    def __ior__(self, other: Mapping[str, str]) -> _ResponseHeaders:  # noqa: PYI034 - Python 3.10
        self.update(other)
        return self

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
        self._background: BackgroundTask | BackgroundTasks | None = None
        self.background = background

    @property
    def background(self) -> BackgroundTask | BackgroundTasks | None:
        return self._background

    @background.setter
    def background(
        self,
        value: BackgroundTask | BackgroundTasks | Callable[[], Any] | None,
    ) -> None:
        if value is not None and not callable(value):
            raise TypeError("background must be callable")
        self._background = (
            value
            if value is None or isinstance(value, (BackgroundTask, BackgroundTasks))
            else BackgroundTask(value)
        )

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

    @property
    def data(self) -> bytes:
        return self.body

    @data.setter
    def data(self, value: str | bytes) -> None:
        self.set_data(value)

    def get_data(self, as_text: bool = False) -> bytes | str:
        return self.text if as_text else self.body

    def set_data(self, value: str | bytes) -> None:
        self.body = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        if "content-length" in self.headers:
            self.headers["content-length"] = str(len(self.body))

    @property
    def is_json(self) -> bool:
        mime = (self.headers.get("content-type") or self.media_type or "").split(";", 1)[0]
        mime = mime.strip().lower()
        return mime == "application/json" or (
            mime.startswith("application/") and mime.endswith("+json")
        )

    def get_json(self, force: bool = False, silent: bool = False) -> Any:
        if not force and not self.is_json:
            return None
        try:
            return json.loads(self.body)
        except (ValueError, RecursionError):
            if silent:
                return None
            raise

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
        max_age: int | timedelta | None = None,
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
        for attr_name, attr_value in (("path", path), ("domain", domain)):
            if attr_value is not None and any(ch in attr_value for ch in ";\r\n\x00"):
                raise ValueError(f"cookie {attr_name} contains invalid characters")
        if isinstance(expires, str) and any(ch in expires for ch in ";\r\n\x00"):
            raise ValueError("cookie expires contains invalid characters")
        cookie = SimpleCookie()
        cookie[key] = value
        morsel = cookie[key]
        if max_age is not None:
            if isinstance(max_age, timedelta):
                morsel["max-age"] = str(int(max_age.total_seconds()))
            elif isinstance(max_age, bool) or not isinstance(max_age, int):
                raise ValueError("max_age must be an int or timedelta")
            else:
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

    def delete_cookie(
        self,
        key: str,
        *,
        path: str = "/",
        domain: str | None = None,
        secure: bool = False,
        httponly: bool = False,
        samesite: str | None = "Lax",
        partitioned: bool = False,
    ) -> None:
        self.set_cookie(
            key,
            "",
            max_age=0,
            expires=datetime(1970, 1, 1, tzinfo=timezone.utc),
            path=path,
            domain=domain,
            secure=secure or partitioned or key.startswith(("__Secure-", "__Host-", "__Http-")),
            httponly=httponly or key.startswith(("__Http-", "__Host-Http-")),
            samesite=samesite,
            partitioned=partitioned,
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
        encoded_name = quote(download_name, safe="")
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
        response_headers = {
            key: value
            for key, value in (headers or {}).items()
            if content_length is not None or str(key).lower() != "content-length"
        }
        if content_length is not None:
            response_headers["content-length"] = str(content_length)
        super().__init__(b"", status, response_headers, media_type, background)
        self.content_length = content_length
        self._content = content
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
        self._closed = False

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = self._finished = True
        targets = [self._iterator]
        if self._content is not self._iterator:
            targets.append(self._content)
        failure: BaseException | None = None
        for target in targets:
            close = getattr(target, "aclose", None) or getattr(target, "close", None)
            if close is not None:
                try:
                    self._context.run(lambda close=close: _resolve_stream_awaitable(close()))
                except BaseException as error:  # noqa: BLE001 - close every stream handle
                    failure = failure or error
        if failure is not None:
            raise failure

    async def _aclose(self) -> None:
        if self._closed:
            return
        self._closed = self._finished = True
        targets = [self._iterator]
        if self._content is not self._iterator:
            targets.append(self._content)
        failure: BaseException | None = None
        for target in targets:
            close = getattr(target, "aclose", None) or getattr(target, "close", None)
            if close is not None:
                try:
                    result = self._context.run(close)
                    if inspect.isawaitable(result):
                        await result
                except BaseException as error:  # noqa: BLE001 - close every stream handle
                    failure = failure or error
        if failure is not None:
            raise failure

    def _finish(self, background: Callable[[], Any] | None) -> None:
        self.background = None
        try:
            self._close()
        finally:
            if background is not None:
                _resolve_stream_awaitable(background())

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


_SSE_LINE_BREAK_RE = re.compile(r"\r\n|\r|\n")


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
            lines.extend(f": {line}" for line in _SSE_LINE_BREAK_RE.split(str(self.comment)))
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
        lines.extend(f"data: {line}" for line in _SSE_LINE_BREAK_RE.split(data))
        return ("\n".join(lines) + "\n\n").encode("utf-8")


class EventSourceResponse(StreamingResponse):
    def __init__(
        self,
        content: Iterable[Any] | AsyncIterable[Any],
        *,
        status: int = 200,
        headers: ResponseHeaders | None = None,
        background: BackgroundTask | BackgroundTasks | Callable[[], Any] | None = None,
    ) -> None:
        super().__init__(
            content,
            status=status,
            headers={
                "cache-control": "no-cache",
                "x-accel-buffering": "no",
            },
            media_type="text/event-stream; charset=utf-8",
            background=background,
        )
        filtered = [
            (key, value)
            for key, value in _header_pairs(headers)
            if str(key).lower() != "content-length"
        ]
        _apply_headers(self, filtered)

    def _encode_chunk(self, chunk: Any) -> bytes:
        event = chunk if isinstance(chunk, ServerSentEvent) else ServerSentEvent(data=chunk)
        return event.encode()


def _sse_single_line(value: Any, field: str) -> str:
    text = str(value)
    if "\r" in text or "\n" in text or "\x00" in text:
        raise ValueError(f"SSE {field} must not contain line breaks or NUL")
    return text


def _resolve_stream_awaitable(value: Any) -> Any:
    return resolve_awaitable(value)


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


ResponseHeaders = Mapping[str, str] | Iterable[tuple[str, str]]


def _header_pairs(headers: ResponseHeaders | None) -> list[tuple[str, str]]:
    if headers is None:
        return []
    if isinstance(headers, Mapping):
        return [(str(key), str(value)) for key, value in headers.items()]
    if isinstance(headers, (str, bytes, bytearray)):
        raise TypeError("response headers must be a mapping or iterable of (name, value) pairs")
    pairs: list[tuple[str, str]] = []
    for item in headers:
        try:
            key, value = item
        except (TypeError, ValueError) as exc:
            raise TypeError(
                "response headers must be a mapping or iterable of (name, value) pairs"
            ) from exc
        pairs.append((str(key), str(value)))
    return pairs


def _apply_headers(response: Response, headers: ResponseHeaders | None) -> None:
    seen: set[str] = set()
    for name, value in _header_pairs(headers):
        normalized, text = _validate_header(name, value)
        if normalized in seen:
            response.append_header(normalized, text)
        else:
            response.headers[normalized] = text
            seen.add(normalized)


def make_response(
    value: ResponseValue = b"",
    status: int | None = None,
    headers: ResponseHeaders | None = None,
) -> Response | FileResponse | TemplateResponse:
    tuple_headers: ResponseHeaders | None = None
    if isinstance(value, tuple):
        if len(value) == 2:
            value, tuple_status = value
            if isinstance(tuple_status, Mapping) or (
                isinstance(tuple_status, Iterable)
                and not isinstance(tuple_status, (str, bytes, bytearray))
            ):
                tuple_headers = tuple_status
            else:
                status = int(tuple_status)
        elif len(value) == 3:
            value, tuple_status, tuple_headers = value
            status = int(tuple_status)
        else:
            raise TypeError(
                "response tuple must be (body, status), (body, headers) or (body, status, headers)"
            )

    if isinstance(value, (Response, FileResponse, TemplateResponse)):
        response: Response = value
        if status is not None:
            response.status = status
        _apply_headers(response, tuple_headers)
        _apply_headers(response, headers)
        return response

    final_status = 200 if status is None else status
    if isinstance(value, (dict, list)) or (
        hasattr(value, "__dataclass_fields__") and not isinstance(value, type)
    ):
        response = JSONResponse(value, final_status)
    elif value is None:
        response = Response(b"", final_status)
    elif isinstance(value, str):
        response = HTMLResponse(value, final_status)
    else:
        response = Response(value, final_status)
    _apply_headers(response, tuple_headers)
    _apply_headers(response, headers)
    return response


def _jsonify_data(args: tuple[Any, ...], fields: Mapping[str, Any]) -> Any:
    if args and fields:
        raise TypeError("jsonify accepts either positional values or keyword fields")
    if args:
        return args[0] if len(args) == 1 else list(args)
    return dict(fields) if fields else None


def jsonify(*args: Any, **kwargs: Any) -> JSONResponse:
    return JSONResponse(_jsonify_data(args, kwargs))


def redirect(location: str, status: int = 302) -> RedirectResponse:
    return RedirectResponse(location, status)


def render_template(template: str, /, **context: Any) -> TemplateResponse:
    from .app import current_app
    from .context import has_app_context

    if has_app_context():
        current_app.update_template_context(context)
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
