from __future__ import annotations

import json as json_module
import secrets
from collections.abc import Mapping
from http.cookies import SimpleCookie
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit

from .request import Request
from .response import (
    FileResponse,
    HTMLResponse,
    Response,
    StreamingResponse,
    TemplateResponse,
    make_response,
)


class TestClient:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.cookies: dict[str, str] = {}
        self._lifespan_context: Any = None

    def __enter__(self) -> TestClient:  # noqa: PYI034 - Python 3.10 has no typing.Self
        if self._lifespan_context is not None:
            raise RuntimeError("TestClient context is already active")
        self._lifespan_context = self.app.lifespan()
        self._lifespan_context.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if self._lifespan_context is None:
            return False
        context = self._lifespan_context
        self._lifespan_context = None
        return bool(context.__exit__(exc_type, exc_value, traceback))

    def _build_request(
        self,
        path: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        query: Mapping[str, Any] | list[tuple[str, Any]] | None = None,
        query_string: str | bytes | Mapping[str, Any] | list[tuple[str, Any]] | None = None,
        base_url: str | None = None,
        content_type: str | None = None,
        data: str | bytes | Mapping[str, Any] | None = None,
        json: Any = None,
        files: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], Any]:
        if query is not None and query_string is not None:
            raise ValueError("use query or query_string, not both")
        if json is not None and (data is not None or files):
            raise ValueError("json cannot be combined with data or files")
        method = method.upper()
        split = urlsplit(path)
        origin = urlsplit(
            base_url
            or (
                f"{split.scheme or 'http'}://{split.netloc}" if split.netloc else "http://localhost"
            )
        )
        if (
            origin.scheme not in {"http", "https"}
            or not origin.hostname
            or origin.username is not None
            or origin.password is not None
            or origin.path not in {"", "/"}
            or origin.query
            or origin.fragment
        ):
            raise ValueError("base_url must be an HTTP(S) origin without a path or credentials")
        encoded_query = split.query
        provided_query = query if query is not None else query_string
        if provided_query is not None:
            if isinstance(provided_query, bytes):
                encoded_query = provided_query.decode("ascii")
            elif isinstance(provided_query, str):
                encoded_query = provided_query
            else:
                encoded_query = urlencode(provided_query, doseq=True)

        request_headers = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
        request_headers.setdefault("host", origin.netloc)
        if content_type is not None:
            request_headers["content-type"] = content_type
        if self.cookies and "cookie" not in request_headers:
            request_headers["cookie"] = "; ".join(
                f"{key}={value}" for key, value in self.cookies.items()
            )

        if files:
            body, multipart_type = _encode_multipart(data, files)
            request_headers.setdefault("content-type", multipart_type)
        elif json is not None:
            body = json_module.dumps(json, ensure_ascii=False).encode("utf-8")
            request_headers.setdefault("content-type", "application/json")
        elif isinstance(data, Mapping):
            body = urlencode(data, doseq=True).encode("utf-8")
            request_headers.setdefault("content-type", "application/x-www-form-urlencoded")
        elif isinstance(data, str):
            body = data.encode("utf-8")
        else:
            body = data or b""
        request_headers.setdefault("content-length", str(len(body)))

        request_path = split.path or "/"
        if not request_path.startswith("/"):
            request_path = "/" + request_path
        route, params, allowed = self.app._match_route(request_path, method)
        raw = {
            "method": method,
            "path": request_path,
            "scheme": origin.scheme,
            "query_string": encoded_query,
            "query": {},
            "headers": request_headers,
            "raw_headers": [
                (key.encode("latin-1"), value.encode("latin-1"))
                for key, value in request_headers.items()
            ],
            "body": body,
            "text": body.decode("utf-8", errors="replace"),
            "path_params": {key: str(value) for key, value in params.items()},
            "route_pattern": route.rule if route else None,
            "route_error_status": 404 if not allowed else 405,
            "allowed_methods": list(allowed),
            "client_addr": "127.0.0.1",
        }
        return raw, route

    def open(
        self,
        path: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        query: Mapping[str, Any] | list[tuple[str, Any]] | None = None,
        query_string: str | bytes | Mapping[str, Any] | list[tuple[str, Any]] | None = None,
        base_url: str | None = None,
        content_type: str | None = None,
        data: str | bytes | Mapping[str, Any] | None = None,
        json: Any = None,
        files: Mapping[str, Any] | None = None,
        follow_redirects: bool = False,
        max_redirects: int = 20,
    ) -> Response:
        if max_redirects < 1:
            raise ValueError("max_redirects must be positive")
        raw, route = self._build_request(
            path,
            method=method,
            headers=headers,
            query=query,
            query_string=query_string,
            base_url=base_url,
            content_type=content_type,
            data=data,
            json=json,
            files=files,
        )
        method = raw["method"]

        if route is not None:
            if route.callback is None:
                raise RuntimeError(f"route {route.endpoint!r} has no callback")
            value = route.callback(raw)
        else:
            value = self.app._fallback_callback(raw)
        response = self._materialize(value)

        if method == "HEAD":
            response.headers.setdefault("content-length", str(len(response.body)))
            response.body = b""
        elif response.status_code in {204, 205, 304}:
            response.headers.pop("content-length", None)
            response.body = b""
        self._update_cookies(response)
        if response.background is not None:
            response.background()
            response.background = None
        if follow_redirects:
            redirect_headers = dict(headers or {})
            if content_type is not None:
                redirect_headers["content-type"] = content_type
            return self._follow_redirects(
                response,
                request_url=Request(raw).url,
                method=method,
                headers=redirect_headers,
                data=data,
                json=json,
                files=files,
                max_redirects=max_redirects,
            )
        return response

    def _follow_redirects(
        self,
        response: Response,
        *,
        request_url: str,
        method: str,
        headers: Mapping[str, str] | None,
        data: str | bytes | Mapping[str, Any] | None,
        json: Any,
        files: Mapping[str, Any] | None,
        max_redirects: int,
    ) -> Response:
        history: list[Response] = []
        visited: set[tuple[str, str]] = set()
        current = response
        current_method = method
        current_data = data
        current_json = json
        current_files = files
        current_headers = {str(key).lower(): str(value) for key, value in (headers or {}).items()}

        while current.status_code in {301, 302, 303, 307, 308} and "location" in current.headers:
            if len(history) >= max_redirects:
                raise RuntimeError(f"redirect limit exceeded ({max_redirects})")
            location = current.headers["location"]
            target = urljoin(request_url, location)
            split = urlsplit(target)
            if (
                split.scheme not in {"http", "https"}
                or split.netloc != urlsplit(request_url).netloc
            ):
                raise RuntimeError("test client does not follow external redirects")
            request_url = target

            if current.status_code == 303 or (
                current.status_code in {301, 302} and current_method not in {"GET", "HEAD"}
            ):
                current_method = "HEAD" if current_method == "HEAD" else "GET"
                current_data = None
                current_json = None
                current_files = None
                current_headers.pop("content-type", None)
                current_headers.pop("Content-Type", None)
                current_headers.pop("content-length", None)
                current_headers.pop("Content-Length", None)

            key = (current_method, target)
            if key in visited:
                raise RuntimeError(f"redirect loop detected at {location!r}")
            visited.add(key)
            history.append(current)
            current = self.open(
                target,
                method=current_method,
                headers=current_headers,
                data=current_data,
                json=current_json,
                files=current_files,
                follow_redirects=False,
            )

        current.history = tuple(history)
        return current

    def get(self, path: str, **kwargs: Any) -> Response:
        return self.open(path, method="GET", **kwargs)

    def post(self, path: str, **kwargs: Any) -> Response:
        return self.open(path, method="POST", **kwargs)

    def put(self, path: str, **kwargs: Any) -> Response:
        return self.open(path, method="PUT", **kwargs)

    def patch(self, path: str, **kwargs: Any) -> Response:
        return self.open(path, method="PATCH", **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Response:
        return self.open(path, method="DELETE", **kwargs)

    def options(self, path: str, **kwargs: Any) -> Response:
        return self.open(path, method="OPTIONS", **kwargs)

    def head(self, path: str, **kwargs: Any) -> Response:
        return self.open(path, method="HEAD", **kwargs)

    def _materialize(self, value: Any) -> Response:
        response = make_response(value)
        if isinstance(response, StreamingResponse):
            chunks = []
            while True:
                chunk = response._next_chunk()
                if chunk is None:
                    break
                chunks.append(chunk)
            materialized = Response(
                b"".join(chunks),
                response.status_code,
                response.headers,
                response.media_type,
                response.background,
            )
            materialized._extra_headers.extend(response._extra_headers)
            return materialized
        if isinstance(response, TemplateResponse):
            html = self.app._core.render_template(response.template, response.context_json)
            materialized = HTMLResponse(html, response.status_code, response.headers)
            materialized.background = response.background
            materialized._extra_headers.extend(response._extra_headers)
            return materialized
        if isinstance(response, FileResponse):
            with Path(response.path).open("rb") as handle:
                handle.seek(response.offset)
                body = handle.read(response.length)
            materialized = Response(
                body,
                response.status_code,
                response.headers,
                response.media_type,
                response.background,
            )
            materialized._extra_headers.extend(response._extra_headers)
            return materialized
        return response

    def _update_cookies(self, response: Response) -> None:
        for header_name, raw_cookie in response.header_items:
            if header_name.lower() != "set-cookie":
                continue
            parsed = SimpleCookie()
            parsed.load(raw_cookie)
            for name, morsel in parsed.items():
                if morsel["max-age"] == "0":
                    self.cookies.pop(name, None)
                else:
                    self.cookies[name] = morsel.value


def _encode_multipart(
    data: str | bytes | Mapping[str, Any] | None,
    files: Mapping[str, Any],
) -> tuple[bytes, str]:
    boundary = f"higuma-{secrets.token_hex(12)}"
    chunks: list[bytes] = []

    if isinstance(data, Mapping):
        for name, value in data.items():
            values = value if isinstance(value, (list, tuple)) else (value,)
            for item in values:
                chunks.extend(
                    [
                        f"--{boundary}\r\n".encode(),
                        (f'Content-Disposition: form-data; name="{name}"\r\n\r\n').encode(),
                        str(item).encode("utf-8"),
                        b"\r\n",
                    ]
                )

    for name, value in files.items():
        values = value if isinstance(value, list) else [value]
        for item in values:
            filename, content, content_type = _normalize_test_file(item)
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    (
                        f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                    ).encode(),
                    f"Content-Type: {content_type}\r\n\r\n".encode(),
                    content,
                    b"\r\n",
                ]
            )

    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _normalize_test_file(value: Any) -> tuple[str, bytes, str]:
    if not isinstance(value, tuple) or len(value) not in (2, 3):
        raise TypeError(
            "test files must be (filename, content) or (filename, content, content_type)"
        )
    filename = str(value[0])
    raw_content = value[1]
    if hasattr(raw_content, "read"):
        raw_content = raw_content.read()
    content = raw_content.encode("utf-8") if isinstance(raw_content, str) else bytes(raw_content)
    content_type = str(value[2]) if len(value) == 3 else "application/octet-stream"
    return filename, content, content_type
