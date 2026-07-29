from __future__ import annotations

import io
import sys
from collections.abc import Callable
from typing import Any

from .request import Request
from .response import Response


def wsgi_view(application: Callable[..., Any], prefix: str) -> Callable[[Request], Response]:
    def mounted(request: Request) -> Response:
        status = "500 Internal Server Error"
        response_headers: list[tuple[str, str]] = []

        def start_response(
            status_line: str,
            headers: list[tuple[str, str]],
            exc_info: Any = None,
        ) -> Callable[[bytes], None]:
            nonlocal status, response_headers
            if exc_info is not None and response_headers:
                raise exc_info[1].with_traceback(exc_info[2])
            status = status_line
            response_headers = headers
            return body_chunks.append

        body_chunks: list[bytes] = []
        environ = _wsgi_environ(request, prefix)
        result = application(environ, start_response)
        try:
            body_chunks.extend(bytes(chunk) for chunk in result)
        finally:
            close = getattr(result, "close", None)
            if close is not None:
                close()
        return _mounted_response(
            b"".join(body_chunks),
            int(status.split(" ", 1)[0]),
            response_headers,
        )

    return mounted


def asgi_view(application: Callable[..., Any], prefix: str) -> Callable[[Request], Any]:
    async def mounted(request: Request) -> Response:
        events: list[dict[str, Any]] = []
        received = False

        async def receive() -> dict[str, Any]:
            nonlocal received
            if received:
                return {"type": "http.disconnect"}
            received = True
            return {
                "type": "http.request",
                "body": request.body,
                "more_body": False,
            }

        async def send(event: dict[str, Any]) -> None:
            events.append(event)

        await application(_asgi_scope(request, prefix), receive, send)
        start = next(
            (event for event in events if event.get("type") == "http.response.start"),
            None,
        )
        if start is None:
            raise RuntimeError("mounted ASGI app did not send http.response.start")
        body = b"".join(
            event.get("body", b"") for event in events if event.get("type") == "http.response.body"
        )
        headers = [
            (bytes(key).decode("latin-1"), bytes(value).decode("latin-1"))
            for key, value in start.get("headers", [])
        ]
        return _mounted_response(body, int(start.get("status", 200)), headers)

    return mounted


def _mounted_path(path: str, prefix: str) -> str:
    value = path[len(prefix) :] if prefix != "/" and path.startswith(prefix) else path
    return value if value.startswith("/") else f"/{value}"


def _wsgi_environ(request: Request, prefix: str) -> dict[str, Any]:
    host, _, port = str(request.headers.get("host", "localhost")).partition(":")
    environ: dict[str, Any] = {
        "REQUEST_METHOD": request.method,
        "SCRIPT_NAME": "" if prefix == "/" else prefix,
        "PATH_INFO": _mounted_path(request.path, prefix),
        "QUERY_STRING": request.query_string,
        "SERVER_NAME": host,
        "SERVER_PORT": port or "80",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "https"
        if request.headers.get("x-forwarded-proto") == "https"
        else "http",
        "wsgi.input": io.BytesIO(request.body),
        "wsgi.errors": sys.stderr,
        "wsgi.multithread": True,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
    }
    if request.content_type:
        environ["CONTENT_TYPE"] = request.content_type
    if request.content_length is not None:
        environ["CONTENT_LENGTH"] = str(request.content_length)
    for key, value in request.headers.items():
        normalized = key.upper().replace("-", "_")
        if normalized not in {"CONTENT_TYPE", "CONTENT_LENGTH"}:
            environ[f"HTTP_{normalized}"] = value
    return environ


def _asgi_scope(request: Request, prefix: str) -> dict[str, Any]:
    host, _, port = str(request.headers.get("host", "localhost")).partition(":")
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": request.method,
        "scheme": "https" if request.headers.get("x-forwarded-proto") == "https" else "http",
        "path": _mounted_path(request.path, prefix),
        "raw_path": _mounted_path(request.path, prefix).encode(),
        "root_path": "" if prefix == "/" else prefix,
        "query_string": request.query_string.encode(),
        "headers": [
            (key.encode("latin-1"), value.encode("latin-1"))
            for key, value in request.headers.items()
        ],
        "server": (host, int(port or 80)),
        "client": None,
        "state": request.state,
    }


def _header(headers: list[tuple[str, str]], name: str) -> str | None:
    name = name.lower()
    return next((value for key, value in headers if key.lower() == name), None)


def _mounted_response(
    body: bytes,
    status: int,
    headers: list[tuple[str, str]],
) -> Response:
    first_headers: dict[str, str] = {}
    extra_headers: list[tuple[str, str]] = []
    for key, value in headers:
        normalized = key.lower()
        if normalized in first_headers:
            extra_headers.append((normalized, value))
        else:
            first_headers[normalized] = value
    response = Response(body, status, first_headers, _header(headers, "content-type"))
    response._extra_headers.extend(extra_headers)
    return response
