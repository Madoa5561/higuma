from __future__ import annotations

import asyncio
import json
import math
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlsplit

from .request import Request
from .response import Response, _ResponseHeaders
from .testing import TestClient, _validate_origin
from .websocket import WebSocketDisconnect


class _Exchange:
    def __init__(self, application: Any, scope: dict[str, Any], timeout: float) -> None:
        self.timeout = timeout
        self.kind = scope["type"]
        self.incoming: asyncio.Queue[Any] = asyncio.Queue(maxsize=4)
        self.outgoing: asyncio.Queue[Any] = asyncio.Queue(maxsize=1)
        self.closing = False

        async def send(event):
            if self.closing:
                raise OSError("test client disconnected")
            await self.outgoing.put(event)

        self.task = asyncio.create_task(application(scope, self.incoming.get, send))

    async def event(self) -> dict[str, Any]:
        receive = asyncio.create_task(self.outgoing.get())
        try:
            done, _ = await asyncio.wait(
                {receive, self.task}, timeout=self.timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if receive in done:
                return receive.result()
            if self.task in done:
                self.task.result()
                if not self.outgoing.empty():
                    return self.outgoing.get_nowait()
                raise RuntimeError("ASGI application ended before the expected event")
            raise TimeoutError("ASGI test client timed out waiting for an event")
        finally:
            if not receive.done():
                receive.cancel()
            await asyncio.gather(receive, return_exceptions=True)

    async def finish(self) -> None:
        done, _ = await asyncio.wait({self.task}, timeout=self.timeout)
        if not done:
            raise TimeoutError("ASGI application did not finish cleanup")
        self.task.result()

    async def close(self, disconnect: dict[str, Any]) -> None:
        self.closing = True
        while not self.incoming.empty():
            self.incoming.get_nowait()
        self.incoming.put_nowait(disconnect)
        while not self.outgoing.empty():
            self.outgoing.get_nowait()
        done, _ = await asyncio.wait({self.task}, timeout=self.timeout)
        if not done:
            self.task.cancel()
            done, _ = await asyncio.wait({self.task}, timeout=self.timeout)
        if not done:
            raise TimeoutError(
                "ASGI cleanup is blocked; synchronous work must have its own timeout"
            )
        if not self.task.cancelled():
            self.task.result()


class ASGIStreamResponse:
    def __init__(self, exchange: _Exchange, event: dict[str, Any]) -> None:
        self._exchange = exchange
        self._consumed = False
        self.status_code = event["status"]
        self.raw_headers = tuple(event.get("headers", ()))
        self.headers = _ResponseHeaders(
            {key.decode("latin-1"): value.decode("latin-1") for key, value in self.raw_headers}
        )

    async def aiter_bytes(self):
        if self._consumed:
            raise RuntimeError("response stream has already been consumed")
        self._consumed = True
        while True:
            event = await self._exchange.event()
            if event["type"] != "http.response.body":
                raise RuntimeError("expected http.response.body")
            if event.get("body"):
                yield event["body"]
            if not event.get("more_body", False):
                await self._exchange.finish()
                return


class ASGITestWebSocket:
    def __init__(self, exchange: _Exchange, headers: list[Any]) -> None:
        self._exchange = exchange
        self.headers = headers
        self.closed = False

    async def send_text(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("WebSocket text payload must be str")
        if self.closed:
            raise WebSocketDisconnect(1006)
        await self._exchange.incoming.put({"type": "websocket.receive", "text": value})

    async def send_bytes(self, value: bytes | bytearray | memoryview) -> None:
        if isinstance(value, bytes):
            payload = value
        elif isinstance(value, (bytearray, memoryview)):
            payload = bytes(value)
        else:
            raise TypeError("WebSocket binary payload must be bytes-like")
        if self.closed:
            raise WebSocketDisconnect(1006)
        await self._exchange.incoming.put({"type": "websocket.receive", "bytes": payload})

    async def send_json(self, value: Any) -> None:
        await self.send_text(json.dumps(value, ensure_ascii=False))

    async def receive(self) -> str | bytes:
        event = await self._exchange.event()
        if event["type"] == "websocket.close":
            self.closed = True
            raise WebSocketDisconnect(event.get("code", 1000), event.get("reason", ""))
        if event["type"] != "websocket.send":
            raise RuntimeError("expected websocket.send")
        return event["text"] if event.get("text") is not None else event.get("bytes", b"")

    async def receive_text(self) -> str:
        value = await self.receive()
        if not isinstance(value, str):
            raise TypeError("expected a text WebSocket message")
        return value

    async def receive_bytes(self) -> bytes:
        value = await self.receive()
        if not isinstance(value, bytes):
            raise TypeError("expected a binary WebSocket message")
        return value

    async def receive_json(self) -> Any:
        return json.loads(await self.receive_text())

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if not self.closed:
            self.closed = True
            await self._exchange.close(
                {"type": "websocket.disconnect", "code": code, "reason": reason}
            )


class ASGITestClient:
    """Asyncio protocol client with lifespan, bounded response streaming, and WebSockets."""

    def __init__(
        self,
        app: Any,
        *,
        base_url: str = "http://localhost",
        root_path: str = "",
        timeout: float = 5,
    ) -> None:
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive finite number")
        _validate_origin(base_url)
        if not isinstance(root_path, str):
            raise TypeError("root_path must be a string")
        normalized_root = root_path.rstrip("/")
        if normalized_root and not normalized_root.startswith("/"):
            normalized_root = f"/{normalized_root}"
        self.app, self.base_url, self.root_path, self.timeout = (
            app,
            base_url,
            normalized_root,
            timeout,
        )
        self._helper = TestClient(app)
        self.cookies = self._helper.cookies
        self._lifespan: _Exchange | None = None
        self._state: dict[str, Any] = {}
        self._active: set[_Exchange] = set()

    async def __aenter__(self):
        if self._lifespan is not None:
            raise RuntimeError("ASGI test client is already active")
        exchange = _Exchange(
            self.app.asgi_app,
            {"type": "lifespan", "asgi": {"version": "3.0"}, "state": self._state},
            self.timeout,
        )
        self._lifespan = exchange
        await exchange.incoming.put({"type": "lifespan.startup"})
        try:
            event = await exchange.event()
            if event["type"] != "lifespan.startup.complete":
                raise RuntimeError(event.get("message", "ASGI startup failed"))
        except BaseException:
            self._lifespan = None
            exchange.task.cancel()
            await asyncio.gather(exchange.task, return_exceptions=True)
            raise
        return self

    async def __aexit__(self, *exc):
        close_error: BaseException | None = None
        try:
            active = list(self._active)
            self._active.clear()
            for exchange in active:
                try:
                    await exchange.close({"type": exchange.kind + ".disconnect", "code": 1000})
                except BaseException as error:  # noqa: BLE001 - close every active exchange
                    close_error = close_error or error
        finally:
            exchange, self._lifespan = self._lifespan, None
            if exchange is not None:
                try:
                    await exchange.incoming.put({"type": "lifespan.shutdown"})
                    event = await exchange.event()
                    await exchange.finish()
                    if event["type"] != "lifespan.shutdown.complete":
                        raise RuntimeError(event.get("message", "ASGI shutdown failed"))
                finally:
                    if not exchange.task.done():
                        exchange.task.cancel()
                        await asyncio.gather(exchange.task, return_exceptions=True)
        if close_error is not None:
            raise close_error

    def _scope(self, path: str, *, method: str = "GET", **kwargs):
        explicit_base_url = kwargs.pop("base_url", None)
        split = urlsplit(path)
        effective_base_url = explicit_base_url or (
            f"{split.scheme or 'http'}://{split.netloc}" if split.netloc else self.base_url
        )
        raw, _ = self._helper._build_request(
            path, method=method, base_url=effective_base_url, **kwargs
        )
        origin = urlsplit(effective_base_url)
        encoded = quote(self.root_path, safe="/") + quote(raw["path"], safe="/%!$&'()*+,-.:;=@_~")
        return raw, {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.5"},
            "http_version": "1.1",
            "scheme": raw["scheme"],
            "method": raw["method"],
            "path": unquote(encoded),
            "raw_path": encoded.encode("ascii"),
            "root_path": self.root_path,
            "query_string": raw["query_string"].encode("ascii"),
            "headers": raw["raw_headers"],
            "client": ("127.0.0.1", 1234),
            "server": (origin.hostname, origin.port or (443 if origin.scheme == "https" else 80)),
            "state": self._state.copy(),
        }

    @asynccontextmanager
    async def stream(self, path: str, *, method: str = "GET", **kwargs):
        raw, scope = self._scope(path, method=method, **kwargs)
        exchange = _Exchange(self.app.asgi_app, scope, self.timeout)
        self._active.add(exchange)
        exchange.incoming.put_nowait({"type": "http.request", "body": raw["body"]})
        try:
            event = await exchange.event()
            if event["type"] != "http.response.start":
                raise RuntimeError("expected http.response.start")
            response = ASGIStreamResponse(exchange, event)
            cookies = Response()
            for key, value in response.raw_headers:
                cookies.append_header(key.decode("latin-1"), value.decode("latin-1"))
            self._helper._update_cookies(cookies)
            yield response
        finally:
            self._active.discard(exchange)
            await exchange.close({"type": "http.disconnect"})

    async def open(
        self,
        path: str,
        *,
        method: str = "GET",
        follow_redirects: bool = False,
        max_redirects: int = 20,
        **kwargs,
    ) -> Response:
        if (
            isinstance(max_redirects, bool)
            or not isinstance(max_redirects, int)
            or max_redirects < 1
        ):
            raise ValueError("max_redirects must be a positive integer")
        raw, scope = self._scope(path, method=method, **kwargs)
        async with self.stream(path, method=method, **kwargs) as streamed:
            chunks = [chunk async for chunk in streamed.aiter_bytes()]
            response = Response(
                b"".join(chunks),
                streamed.status_code,
                media_type=streamed.headers.get("content-type"),
            )
            for key, value in streamed.raw_headers:
                name, text = key.decode("latin-1"), value.decode("latin-1")
                if name in response.headers:
                    response.append_header(name, text)
                else:
                    response.headers[name] = text
        if not follow_redirects:
            return response
        request_path = scope["raw_path"].decode("ascii")
        request_query = f"?{raw['query_string']}" if raw["query_string"] else ""
        request_url = f"{raw['scheme']}://{Request(raw).host}{request_path}{request_query}"
        return await self._follow_redirects(
            response,
            request_url=request_url,
            method=raw["method"],
            max_redirects=max_redirects,
            **kwargs,
        )

    async def _follow_redirects(
        self,
        response: Response,
        *,
        request_url: str,
        method: str,
        max_redirects: int,
        **kwargs: Any,
    ) -> Response:
        history: list[Response] = []
        visited: set[tuple[str, str]] = set()
        current = response
        current_method = method
        current_kwargs = dict(kwargs)
        current_kwargs.pop("query", None)
        current_kwargs.pop("query_string", None)
        current_kwargs.pop("base_url", None)
        current_headers = {
            str(key).lower(): str(value)
            for key, value in (current_kwargs.get("headers") or {}).items()
        }
        if current_kwargs.get("content_type") is not None:
            current_headers["content-type"] = str(current_kwargs.pop("content_type"))

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
                current_kwargs.pop("data", None)
                current_kwargs.pop("json", None)
                current_kwargs.pop("files", None)
                current_headers.pop("content-type", None)
                current_headers.pop("content-length", None)

            next_target = target
            if self.root_path:
                decoded_path = unquote(split.path)
                count = len(self.root_path.split("/")) - 1
                parts = split.path.split("/")
                if (
                    decoded_path == self.root_path
                    and len(parts) == count + 1
                    and unquote("/".join(parts[: count + 1])) == self.root_path
                ):
                    stripped_path = "/"
                elif (
                    decoded_path.startswith(self.root_path + "/")
                    and len(parts) > count + 1
                    and unquote("/".join(parts[: count + 1])) == self.root_path
                ):
                    stripped_path = "/" + "/".join(parts[count + 1 :])
                else:
                    raise RuntimeError("test client does not follow redirects outside root_path")
                query = f"?{split.query}" if split.query else ""
                next_target = f"{split.scheme}://{split.netloc}{stripped_path}{query}"

            key = (current_method, target)
            if key in visited:
                raise RuntimeError(f"redirect loop detected at {location!r}")
            visited.add(key)
            history.append(current)
            current_kwargs["headers"] = current_headers
            current = await self.open(
                next_target,
                method=current_method,
                follow_redirects=False,
                **current_kwargs,
            )

        current.history = tuple(history)
        return current

    async def get(self, path: str, **kwargs):
        return await self.open(path, method="GET", **kwargs)

    async def post(self, path: str, **kwargs):
        return await self.open(path, method="POST", **kwargs)

    async def put(self, path: str, **kwargs):
        return await self.open(path, method="PUT", **kwargs)

    async def patch(self, path: str, **kwargs):
        return await self.open(path, method="PATCH", **kwargs)

    async def delete(self, path: str, **kwargs):
        return await self.open(path, method="DELETE", **kwargs)

    async def head(self, path: str, **kwargs):
        return await self.open(path, method="HEAD", **kwargs)

    async def options(self, path: str, **kwargs):
        return await self.open(path, method="OPTIONS", **kwargs)

    @asynccontextmanager
    async def websocket_connect(self, path: str, *, headers=None):
        _, scope = self._scope(path, headers=headers)
        scope.update(type="websocket", scheme="wss" if scope["scheme"] == "https" else "ws")
        exchange = _Exchange(self.app.asgi_app, scope, self.timeout)
        self._active.add(exchange)
        exchange.incoming.put_nowait({"type": "websocket.connect"})
        socket = None
        try:
            event = await exchange.event()
            if event["type"] == "websocket.close":
                raise WebSocketDisconnect(event.get("code", 1008), event.get("reason", ""))
            if event["type"] != "websocket.accept":
                raise RuntimeError("expected websocket.accept")
            handshake = Response()
            for key, value in event.get("headers", []):
                handshake.append_header(key.decode("latin-1"), value.decode("latin-1"))
            self._helper._update_cookies(handshake)
            socket = ASGITestWebSocket(exchange, event.get("headers", []))
            yield socket
        finally:
            if socket is not None:
                socket.closed = True
            self._active.discard(exchange)
            await exchange.close({"type": "websocket.disconnect", "code": 1000})
