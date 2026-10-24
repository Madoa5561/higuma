from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack
from contextvars import copy_context
from http import HTTPStatus
from threading import Event, Lock
from typing import Any
from urllib.parse import quote, unquote
from weakref import WeakKeyDictionary

from .exceptions import BadRequest, HTTPException
from .execution import AwaitBridge, await_bridge, resolve_awaitable
from .response import (
    FileResponse,
    HTMLResponse,
    Response,
    StreamingResponse,
    TemplateResponse,
    _validate_header,
)
from .websocket import WebSocketDisconnect

_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def _limit(app: Any) -> int:
    return int(app.config["MAX_CONTENT_LENGTH"])


def _raw_request(
    path: str,
    method: str,
    headers: list[tuple[bytes, bytes]],
    body: bytes,
    *,
    scheme: str,
    query: str,
    peer: str,
    root: str = "",
    scope: Any = None,
) -> dict[str, Any]:
    return {
        "path": path or "/",
        "method": method.upper(),
        "body": body,
        "raw_headers": headers,
        "scheme": scheme,
        "query_string": query,
        "client_addr": peer,
        "root_path": root,
        "scope": scope,
    }


def _dispatch(app: Any, raw: dict[str, Any]) -> Response:
    route, parameters, allowed = app._match_route(raw["path"], raw["method"])
    raw.update(
        path_params={key: str(value) for key, value in parameters.items()},
        route_pattern=route.rule if route else None,
        route_error_status=405 if allowed else 404,
        allowed_methods=allowed,
    )
    response = route.callback(raw) if route is not None else app._fallback_callback(raw)
    if isinstance(response, TemplateResponse):
        try:
            result = HTMLResponse(
                app._core.render_template(response.template, response.context_json),
                response.status_code,
                response.headers,
                response.background,
            )
            result._extra_headers.extend(response._extra_headers)
            return result
        except Exception:
            app.logger.exception("gateway template rendering failed")
            if response.background is not None:
                response.background()
            return app._default_http_error(HTTPException(500))
    return response


def _headers(response: Response, length: int | None) -> list[tuple[str, str]]:
    blocked = _HOP_HEADERS | {
        token.strip().lower() for token in response.headers.get("connection", "").split(",")
    }
    headers = [
        (key, value)
        for key, value in response.header_items
        if key.lower() not in blocked | {"content-length"}
    ]
    if response.status_code in {204, 304}:
        headers = [(key, value) for key, value in headers if key.lower() != "content-type"]
    if (
        response.status_code not in {204, 304}
        and response.media_type
        and not any(key.lower() == "content-type" for key, _ in headers)
    ):
        headers.append(("content-type", response.media_type))
    elif response.status_code not in {204, 304} and not any(
        key.lower() == "content-type" for key, _ in headers
    ):
        headers.append(("content-type", "application/octet-stream"))
    if length is not None and response.status_code not in {204, 304}:
        headers.append(("content-length", str(length)))
    return [_validate_header(key, value) for key, value in headers]


class _Body:
    def __init__(self, response: Response, method: str) -> None:
        self.response = response
        self.closed = False
        self.sent = False
        self.file = None
        self.remaining = None
        if isinstance(response, FileResponse):
            self.file = open(response.path, "rb")  # noqa: SIM115 - ownership ends in close()
            self.file.seek(response.offset)
            self.remaining = response.length
            self.length = response.length
        elif isinstance(response, StreamingResponse):
            self.length = None
        else:
            self.length = len(response.body)
        self.suppress = method == "HEAD" or response.status_code in {204, 205, 304}
        if response.status_code == 205:
            self.length = 0

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        if self.closed or self.suppress:
            raise StopIteration
        if self.file is not None:
            size = 65536 if self.remaining is None else min(65536, self.remaining)
            value = self.file.read(size)
            if self.remaining is not None:
                self.remaining -= len(value)
        elif isinstance(self.response, StreamingResponse):
            value = self.response._next_chunk()
            if value is None:
                raise StopIteration
            return value
        elif not self.sent:
            self.sent = True
            return self.response.body
        else:
            raise StopIteration
        if not value:
            raise StopIteration
        return value

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            if self.file is not None:
                self.file.close()
            if isinstance(self.response, StreamingResponse):
                iterator = self.response._iterator
                close = getattr(iterator, "aclose", None) or getattr(iterator, "close", None)
                if close is not None:
                    self.response._context.run(lambda: resolve_awaitable(close()))
        finally:
            if self.response.background is not None:
                self.response.background()


class _WSGIResult:
    def __init__(self, body: _Body) -> None:
        self.body = body

    def __iter__(self):
        try:
            yield from self.body
        finally:
            self.close()

    def close(self) -> None:
        self.body.close()


class WSGIApplication:
    def __init__(self, app: Any) -> None:
        self.app = app

    def __call__(self, environ: Mapping[str, Any], start_response: Callable[..., Any]):
        try:
            raw = self._request(environ)
            response = _dispatch(self.app, raw)
        except HTTPException as error:
            response = self.app._default_http_error(error)
        except Exception:
            self.app.logger.exception("WSGI dispatch failed")
            response = self.app._default_http_error(HTTPException(500))
        try:
            body = _Body(response, str(environ.get("REQUEST_METHOD", "GET")).upper())
        except OSError:
            if response.background is not None:
                response.background()
            response = self.app._default_http_error(HTTPException(500))
            body = _Body(response, str(environ.get("REQUEST_METHOD", "GET")).upper())
        status = response.status_code
        phrase = HTTPStatus(status).phrase if status in HTTPStatus._value2member_map_ else "Unknown"
        try:
            start_response(f"{status} {phrase}", _headers(response, body.length))
        except BaseException:
            body.close()
            raise
        return _WSGIResult(body)

    def _request(self, environ: Mapping[str, Any]) -> dict[str, Any]:
        headers = []
        for key, value in environ.items():
            if key.startswith("HTTP_"):
                name = key[5:].replace("_", "-").lower()
            elif key in {"CONTENT_TYPE", "CONTENT_LENGTH"} and value != "":
                name = key.replace("_", "-").lower()
            else:
                continue
            headers.append((name.encode("ascii"), str(value).encode("latin-1")))
        if not any(key == b"host" for key, _ in headers):
            host = str(environ.get("SERVER_NAME", "localhost"))
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            port = str(environ.get("SERVER_PORT", "80"))
            headers.append((b"host", f"{host}:{port}".encode("ascii")))
        length_text = environ.get("CONTENT_LENGTH", "")
        if length_text and (not str(length_text).isascii() or not str(length_text).isdigit()):
            raise BadRequest(detail="invalid Content-Length")
        length = int(length_text) if length_text else None
        maximum = _limit(self.app)
        if length is not None and length > maximum:
            raise HTTPException(413)
        source = environ["wsgi.input"]
        chunks = []
        remaining = (
            length
            if length is not None
            else (maximum + 1 if environ.get("wsgi.input_terminated") else 0)
        )
        while remaining:
            chunk = source.read(min(65536, remaining))
            if not chunk:
                if length is not None:
                    raise BadRequest(detail="incomplete request body")
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        body = b"".join(chunks)
        if len(body) > maximum:
            raise HTTPException(413)
        try:
            path = str(environ.get("PATH_INFO", "/")).encode("latin-1").decode("utf-8")
            root = str(environ.get("SCRIPT_NAME", "")).encode("latin-1").decode("utf-8")
        except UnicodeError as error:
            raise BadRequest(detail="invalid WSGI path encoding") from error
        return _raw_request(
            quote(path, safe="/!$&'()*+,-.:;=@_~"),
            str(environ.get("REQUEST_METHOD", "GET")),
            headers,
            body,
            scheme=str(environ.get("wsgi.url_scheme", "http")),
            query=str(environ.get("QUERY_STRING", "")),
            peer=str(environ.get("REMOTE_ADDR", "")),
            root=root,
        )


def _scope_request(scope: Mapping[str, Any], body: bytes = b"") -> dict[str, Any]:
    path = scope.get("raw_path")
    if path is None:
        path = quote(scope.get("path", "/"), safe="/!$&'()*+,-.:;=@_~").encode("ascii")
    path = bytes(path).decode("ascii")
    root = str(scope.get("root_path", "")).rstrip("/")
    decoded = unquote(path)
    if root and (decoded == root or decoded.startswith(root + "/")):
        count = len(root.split("/")) - 1
        parts = path.split("/")
        if unquote("/".join(parts[: count + 1])) != root:
            raise BadRequest(detail="ambiguous ASGI root path")
        path = "/" + "/".join(parts[count + 1 :])
    headers = [(bytes(key).lower(), bytes(value)) for key, value in scope.get("headers", [])]
    if not any(key == b"host" for key, _ in headers):
        host, port = scope.get("server") or ("localhost", None)
        host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        headers.append((b"host", (f"{host}:{port}" if port is not None else host).encode("ascii")))
    client = scope.get("client")
    scheme = {"ws": "http", "wss": "https"}.get(scope.get("scheme"), scope.get("scheme", "http"))
    return _raw_request(
        path,
        scope.get("method", "GET"),
        headers,
        body,
        scheme=scheme,
        query=bytes(scope.get("query_string", b"")).decode("ascii"),
        peer=str(client[0]) if client else "",
        root=root,
        scope=scope,
    )


class ASGIApplication:
    def __init__(self, app: Any) -> None:
        self.app = app
        self._slots: WeakKeyDictionary[Any, asyncio.Semaphore] = WeakKeyDictionary()
        self._lifespan_active = False
        self._lifespan_lock = Lock()
        self._lifespan_loop = None
        self._executor: ThreadPoolExecutor | None = None
        self._executor_lock = Lock()

    async def __call__(self, scope: Mapping[str, Any], receive: Any, send: Any) -> None:
        kind = scope["type"]
        if kind == "lifespan":
            await self._lifespan(scope, receive, send)
            return
        if kind not in {"http", "websocket"}:
            raise ValueError(f"unsupported ASGI scope type: {kind}")
        loop = asyncio.get_running_loop()
        if self._lifespan_loop is not None and self._lifespan_loop is not loop:
            raise RuntimeError("use one Higuma application instance per ASGI event loop")
        if loop not in self._slots:
            count = int(self.app.config.get("ASGI_MAX_CONCURRENCY", 64))
            if count < 1:
                raise ValueError("ASGI_MAX_CONCURRENCY must be positive")
            self._slots[loop] = asyncio.Semaphore(count)
        async with self._slots[loop]:
            if kind == "http":
                await self._http(scope, receive, send)
            else:
                await self._websocket(scope, receive, send)

    async def _lifespan(self, scope: Any, receive: Any, send: Any) -> None:
        event = await receive()
        if event["type"] != "lifespan.startup":
            raise RuntimeError("expected lifespan.startup")
        with self._lifespan_lock:
            active = self._lifespan_active
            if not active:
                self._lifespan_active = True
                self._lifespan_loop = asyncio.get_running_loop()
        if active:
            await send({"type": "lifespan.startup.failed", "message": "lifespan already active"})
            return
        started = False
        try:
            async with AsyncExitStack() as stack:
                if self.app._lifespan_handler is not None:
                    context = self.app._lifespan_handler(self.app)
                    if hasattr(context, "__aenter__"):
                        state = await stack.enter_async_context(context)
                    else:
                        state = stack.enter_context(context)
                    if state is not None:
                        if not isinstance(state, Mapping):
                            raise TypeError("lifespan must yield a mapping or None")
                        self.app.state.update(state)
                        if "state" in scope:
                            scope["state"].update(state)
                for handler in self.app._startup_handlers:
                    await self._lifecycle_hook(handler)
                await send({"type": "lifespan.startup.complete"})
                started = True
                event = await receive()
                if event["type"] != "lifespan.shutdown":
                    raise RuntimeError("expected lifespan.shutdown")
                for handler in reversed(self.app._shutdown_handlers):
                    await self._lifecycle_hook(handler)
            await send({"type": "lifespan.shutdown.complete"})
        except Exception:
            self.app.logger.exception("ASGI lifespan failed")
            await send(
                {
                    "type": f"lifespan.{'shutdown' if started else 'startup'}.failed",
                    "message": "Higuma lifecycle failed; see server logs",
                }
            )
        finally:
            with self._lifespan_lock:
                self._lifespan_active = False
                self._lifespan_loop = None
            with self._executor_lock:
                executor, self._executor = self._executor, None
            if executor is not None:
                await asyncio.to_thread(executor.shutdown, wait=True)

    async def _lifecycle_hook(self, handler: Any) -> None:
        args = (self.app,) if inspect.signature(handler).parameters else ()
        result = (
            handler(*args)
            if inspect.iscoroutinefunction(handler)
            else await asyncio.to_thread(handler, *args)
        )
        if inspect.isawaitable(result):
            await result

    async def _worker(self, func: Any, receive: Any) -> None:
        bridge = AwaitBridge(asyncio.get_running_loop())

        def run() -> None:
            token = await_bridge.set(bridge)
            try:
                func()
            finally:
                await_bridge.reset(token)

        async def disconnected() -> None:
            while True:
                event = await receive()
                if event["type"] in {"http.disconnect", "websocket.disconnect"}:
                    return

        with self._executor_lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=int(self.app.config.get("ASGI_MAX_CONCURRENCY", 64)),
                    thread_name_prefix="higuma-asgi",
                )
            executor = self._executor
        context = copy_context()
        worker = asyncio.get_running_loop().run_in_executor(executor, context.run, run)
        watcher = asyncio.create_task(disconnected())
        try:
            done, _ = await asyncio.wait({worker, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                bridge.cancel_pending()
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                if not watcher.done():
                    raise
        except asyncio.CancelledError:
            bridge.cancel_pending()
            await asyncio.shield(worker)
            raise
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            await bridge.close()

    async def _http(self, scope: Any, receive: Any, send: Any) -> None:
        chunks = []
        total = 0
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            if event["type"] != "http.request":
                raise RuntimeError("expected http.request")
            chunk = bytes(event.get("body", b""))
            total += len(chunk)
            if total > _limit(self.app):
                response = self.app._default_http_error(HTTPException(413))
                await send(
                    {
                        "type": "http.response.start",
                        "status": 413,
                        "headers": [
                            (k.encode(), v.encode("latin-1"))
                            for k, v in _headers(response, len(response.body))
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": response.body})
                return
            chunks.append(chunk)
            if not event.get("more_body", False):
                break
        try:
            raw = _scope_request(scope, b"".join(chunks))
        except (UnicodeError, HTTPException):
            response = self.app._default_http_error(BadRequest())
            await send({"type": "http.response.start", "status": 400, "headers": []})
            await send({"type": "http.response.body", "body": response.body})
            return

        def respond() -> None:
            body = None
            started = False
            attempted_send = False
            response = None
            bridge = await_bridge.get()
            try:
                response = _dispatch(self.app, raw)
                body = _Body(response, raw["method"])
                attempted_send = True
                bridge.resolve(
                    send(
                        {
                            "type": "http.response.start",
                            "status": response.status_code,
                            "headers": [
                                (k.encode(), v.encode("latin-1"))
                                for k, v in _headers(response, body.length)
                            ],
                        }
                    )
                )
                started = True
                for chunk in body:
                    bridge.resolve(
                        send({"type": "http.response.body", "body": chunk, "more_body": True})
                    )
                bridge.resolve(
                    send({"type": "http.response.body", "body": b"", "more_body": False})
                )
            except Exception as error:
                if isinstance(error, OSError) and attempted_send:
                    return
                if started:
                    raise
                self.app.logger.exception("ASGI response failed")
                if body is None and response is not None and response.background is not None:
                    response.background()
                    response.background = None
                response = self.app._default_http_error(HTTPException(500))
                bridge.resolve(send({"type": "http.response.start", "status": 500, "headers": []}))
                bridge.resolve(send({"type": "http.response.body", "body": response.body}))
            finally:
                if body is not None:
                    body.close()

        await self._worker(respond, receive)

    async def _websocket(self, scope: Any, receive: Any, send: Any) -> None:
        if (await receive())["type"] != "websocket.connect":
            raise RuntimeError("expected websocket.connect")
        raw = _scope_request(scope)
        route = next(
            (
                rule
                for rule in sorted(
                    self.app._websocket_rules.values(),
                    key=lambda rule: rule.specificity,
                    reverse=True,
                )
                if rule.match(raw["path"]) is not None
            ),
            None,
        )
        if route is None:
            await send({"type": "websocket.close", "code": 1008})
            return
        raw["path_params"] = route.match(raw["path"])
        from .request import Request

        origin = Request(raw).headers.get("origin")
        allowed = route.openapi.get("allowed_origins")
        if origin and allowed and "*" not in allowed and origin not in allowed:
            await send({"type": "websocket.close", "code": 1008})
            return
        queue_size = int(self.app.config.get("ASGI_WEBSOCKET_QUEUE_SIZE", 4))
        message_limit = int(self.app.config.get("ASGI_WEBSOCKET_MAX_MESSAGE_SIZE", 1024 * 1024))
        if queue_size < 1 or message_limit < 1:
            raise ValueError("ASGI WebSocket limits must be positive")
        incoming: asyncio.Queue[Any] = asyncio.Queue(maxsize=queue_size)
        peer_gone = Event()

        async def pump() -> dict[str, Any]:
            event = await receive()
            if event["type"] == "websocket.receive":
                size = (
                    len(event["text"].encode("utf-8"))
                    if event.get("text") is not None
                    else len(event.get("bytes", b""))
                )
                if size > message_limit:
                    await send({"type": "websocket.close", "code": 1009})
                    event = {"type": "websocket.disconnect", "code": 1009}
            if event["type"] == "websocket.disconnect":
                peer_gone.set()
            await incoming.put(event)
            return event

        def serve() -> None:
            bridge = await_bridge.get()
            session = _ASGIWebSocket(bridge, incoming, send, peer_gone, scope)
            preflight = self.app._dispatch_websocket_preflight(route, raw)
            if preflight.status_code != 204:
                if "websocket.http.response" in scope.get("extensions", {}):
                    bridge.resolve(
                        send(
                            {
                                "type": "websocket.http.response.start",
                                "status": preflight.status_code,
                                "headers": [
                                    (k.encode(), v.encode("latin-1"))
                                    for k, v in _headers(preflight, len(preflight.body))
                                ],
                            }
                        )
                    )
                    bridge.resolve(
                        send({"type": "websocket.http.response.body", "body": preflight.body})
                    )
                else:
                    bridge.resolve(send({"type": "websocket.close", "code": 1008}))
                if preflight.background is not None:
                    preflight.background()
                return
            headers = [
                (k.encode(), v.encode("latin-1"))
                for k, v in _headers(preflight, None)
                if not k.startswith("sec-websocket-")
            ]
            accept = {"type": "websocket.accept"}
            version = tuple(
                int(part) for part in scope.get("asgi", {}).get("spec_version", "2.0").split(".")
            )
            if headers and version >= (2, 1):
                accept["headers"] = headers
            elif any(key == b"set-cookie" for key, _ in headers):
                bridge.resolve(send({"type": "websocket.close", "code": 1008}))
                if preflight.background is not None:
                    preflight.background()
                return
            bridge.resolve(send(accept))
            try:
                response = self.app._dispatch_websocket(route, raw, session)
                if response.background is not None:
                    response.background()
                if response.status_code >= 400:
                    session.close(1011, "")
            finally:
                if preflight.background is not None:
                    preflight.background()
                session.close(1000, "")

        await self._worker(serve, pump)


class _ASGIWebSocket:
    def __init__(
        self, bridge: AwaitBridge, incoming: Any, send: Any, peer_gone: Event, scope: Any
    ) -> None:
        self.bridge, self.incoming, self.send_event = bridge, incoming, send
        self.peer_gone = peer_gone
        self.spec_version = tuple(
            int(part) for part in scope.get("asgi", {}).get("spec_version", "2.0").split(".")
        )
        self.closed = False

    async def areceive(self):
        event = await self.incoming.get()
        if event["type"] == "websocket.disconnect":
            self.closed = True
            return "disconnect", None, None, event.get("code", 1000), event.get("reason", "")
        text = event.get("text")
        return (
            ("text", text, None, None, None)
            if text is not None
            else ("bytes", None, event.get("bytes", b""), None, None)
        )

    def receive(self):
        return self.bridge.resolve(self.areceive())

    async def asend_text(self, text):
        if self.closed or self.peer_gone.is_set():
            raise WebSocketDisconnect(1006)
        await self.send_event({"type": "websocket.send", "text": text})

    def send_text(self, text):
        self.bridge.resolve(self.asend_text(text))

    async def asend_bytes(self, data):
        if self.closed or self.peer_gone.is_set():
            raise WebSocketDisconnect(1006)
        await self.send_event({"type": "websocket.send", "bytes": data})

    def send_bytes(self, data):
        self.bridge.resolve(self.asend_bytes(data))

    async def aclose(self, code=1000, reason=""):
        if (
            code not in {1000, 1001, 1002, 1003, 1007, 1008, 1009, 1010, 1011, 1012, 1013, 1014}
            and not 3000 <= code <= 4999
        ):
            raise ValueError("invalid WebSocket close code")
        if len(reason.encode("utf-8")) > 123:
            raise ValueError("WebSocket close reason exceeds 123 bytes")
        if not self.closed and not self.peer_gone.is_set():
            self.closed = True
            event = {"type": "websocket.close", "code": code}
            if self.spec_version >= (2, 3):
                event["reason"] = reason
            await self.send_event(event)

    def close(self, code=1000, reason=""):
        self.bridge.resolve(self.aclose(code, reason))
