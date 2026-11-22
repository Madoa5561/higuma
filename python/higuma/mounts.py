from __future__ import annotations

import asyncio
import io
import sys
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, unquote, unquote_to_bytes

from .request import Request
from .response import Response


def wsgi_view(application: Callable[..., Any], prefix: str) -> Callable[[Request], Response]:
    def mounted(request: Request) -> Response:
        status: str | None = None
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
        if status is None:
            raise RuntimeError("mounted WSGI app did not call start_response")
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
        response_complete = asyncio.Event()
        response_remaining: int | None = None

        async def receive() -> dict[str, Any]:
            nonlocal received
            if received:
                await response_complete.wait()
                return {"type": "http.disconnect"}
            received = True
            return {
                "type": "http.request",
                "body": request.body,
                "more_body": False,
            }

        async def send(event: dict[str, Any]) -> None:
            nonlocal response_remaining
            events.append(event)
            if event.get("type") == "http.response.start":
                if request.method == "HEAD" or int(event.get("status", 200)) in {204, 205, 304}:
                    response_remaining = 0
                else:
                    response_remaining = _parse_content_length(event.get("headers", []))
            elif event.get("type") == "http.response.body":
                if response_remaining is not None:
                    response_remaining -= len(event.get("body", b""))
                if not event.get("more_body", False):
                    response_complete.set()
            if response_remaining is not None and response_remaining <= 0:
                response_complete.set()

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
    if prefix != "/":
        if path == prefix or path.startswith(prefix + "/"):
            value = path[len(prefix) :]
        else:
            encoded_prefix = quote(prefix, safe="/!$&'()*+,-.:;=@_~")
            decoded = unquote(path)
            if path == encoded_prefix or path.startswith(encoded_prefix + "/"):
                value = path[len(encoded_prefix) :]
            elif decoded == prefix or decoded.startswith(prefix + "/"):
                count = len(prefix.split("/")) - 1
                parts = path.split("/")
                value = "/" + "/".join(parts[count + 1 :])
            else:
                value = path
    else:
        value = path
    return value if value.startswith("/") else f"/{value}"


def _wsgi_environ(request: Request, prefix: str) -> dict[str, Any]:
    host, port = _server_address(str(request.headers.get("host", "localhost")), request.scheme)
    environ: dict[str, Any] = {
        "REQUEST_METHOD": request.method,
        "SCRIPT_NAME": (request.root_path + ("" if prefix == "/" else prefix))
        .encode("utf-8")
        .decode("latin-1"),
        "PATH_INFO": unquote_to_bytes(_mounted_path(request.path, prefix)).decode("latin-1"),
        "QUERY_STRING": request.query_string,
        "SERVER_NAME": host,
        "SERVER_PORT": port or "80",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": request.scheme,
        "wsgi.input": io.BytesIO(request.body),
        "wsgi.errors": sys.stderr,
        "wsgi.multithread": True,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "REMOTE_ADDR": request.remote_addr,
    }
    if request.content_type:
        environ["CONTENT_TYPE"] = request.content_type
    if request.content_length is not None:
        environ["CONTENT_LENGTH"] = str(request.content_length)
    combined_headers: dict[str, list[str]] = {}
    for raw_key, raw_value in request.raw_headers:
        key = raw_key.decode("latin-1")
        if "_" in key:
            continue
        value = raw_value.decode("latin-1")
        normalized = key.upper().replace("-", "_")
        if normalized not in {"CONTENT_TYPE", "CONTENT_LENGTH"}:
            combined_headers.setdefault(normalized, []).append(value)
    for normalized, values in combined_headers.items():
        separator = "; " if normalized == "COOKIE" else ","
        environ[f"HTTP_{normalized}"] = separator.join(values)
    return environ


def _asgi_scope(request: Request, prefix: str) -> dict[str, Any]:
    host, port = _server_address(str(request.headers.get("host", "localhost")), request.scheme)
    mounted_path = _mounted_path(request.path, prefix)
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": request.method,
        "scheme": request.scheme,
        "path": unquote(mounted_path),
        "raw_path": quote(mounted_path, safe="/%!$&'()*+,-.:;=@_~").encode("ascii"),
        "root_path": request.root_path + ("" if prefix == "/" else prefix),
        "query_string": request.query_string.encode(),
        "headers": list(request.raw_headers),
        "server": (host, int(port)),
        "client": (request.remote_addr, 0) if request.remote_addr else None,
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
    for key, value in extra_headers:
        response.append_header(key, value)
    return response


def _parse_content_length(headers: Any) -> int | None:
    for key, value in headers:
        if bytes(key).lower() == b"content-length":
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                return None
            return parsed if parsed >= 0 else None
    return None


def _server_address(host_header: str, scheme: str) -> tuple[str, str]:
    value = host_header.strip()
    if value.startswith("[") and "]" in value:
        closing = value.find("]")
        host = value[1:closing]
        port = value[closing + 1 :].removeprefix(":")
    else:
        host, port = value.rsplit(":", 1) if value.count(":") == 1 else (value, "")
    if not port or not port.isascii() or not port.isdigit():
        port = "443" if scheme == "https" else "80"
    return host, port
