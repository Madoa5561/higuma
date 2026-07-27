from __future__ import annotations

import json as json_module
from collections.abc import Mapping
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from .response import FileResponse, HTMLResponse, Response, TemplateResponse, make_response


class TestClient:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.cookies: dict[str, str] = {}

    def open(
        self,
        path: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        query: Mapping[str, Any] | list[tuple[str, Any]] | None = None,
        data: str | bytes | Mapping[str, Any] | None = None,
        json: Any = None,
    ) -> Response:
        method = method.upper()
        split = urlsplit(path)
        query_string = split.query
        if query is not None:
            query_string = urlencode(query, doseq=True)

        request_headers = {
            str(key).lower(): str(value) for key, value in (headers or {}).items()
        }
        request_headers.setdefault("host", "localhost")
        if self.cookies and "cookie" not in request_headers:
            request_headers["cookie"] = "; ".join(
                f"{key}={value}" for key, value in self.cookies.items()
            )

        if json is not None:
            body = json_module.dumps(json, ensure_ascii=False).encode("utf-8")
            request_headers.setdefault("content-type", "application/json")
        elif isinstance(data, Mapping):
            body = urlencode(data, doseq=True).encode("utf-8")
            request_headers.setdefault(
                "content-type", "application/x-www-form-urlencoded"
            )
        elif isinstance(data, str):
            body = data.encode("utf-8")
        else:
            body = data or b""
        request_headers.setdefault("content-length", str(len(body)))

        route, params, allowed = self.app._match_route(split.path or "/", method)
        if method == "OPTIONS" and route is None and allowed:
            response = Response(
                b"",
                204,
                {"allow": ", ".join(allowed)},
            )
            return response

        raw = {
            "method": method,
            "path": split.path or "/",
            "query_string": query_string,
            "query": {},
            "headers": request_headers,
            "body": body,
            "text": body.decode("utf-8", errors="replace"),
            "path_params": {key: str(value) for key, value in params.items()},
            "route_pattern": route.rule if route else None,
            "route_error_status": 404 if not allowed else 405,
            "allowed_methods": list(allowed),
        }

        if route is not None:
            assert route.callback is not None
            value = route.callback(raw)
        else:
            value = self.app._fallback_callback(raw)
        response = self._materialize(value)

        if method == "HEAD":
            response.headers.setdefault("content-length", str(len(response.body)))
            response.body = b""
        self._update_cookies(response)
        return response

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
        if isinstance(response, TemplateResponse):
            html = self.app._core.render_template(
                response.template, response.context_json
            )
            materialized = HTMLResponse(html, response.status_code, response.headers)
            materialized._extra_headers.extend(response._extra_headers)
            return materialized
        if isinstance(response, FileResponse):
            materialized = Response(
                Path(response.path).read_bytes(),
                response.status_code,
                response.headers,
                response.media_type,
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
