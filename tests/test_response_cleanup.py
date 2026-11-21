import asyncio
import io

import pytest
from higuma import Higuma, Request, Response, StreamingResponse, has_request_context, request


def test_raw_async_background_runs_once_with_request_context():
    app = Higuma(__name__, static_folder=None)
    completed = []

    async def after():
        await asyncio.sleep(0)
        completed.append(request.path)

    @app.get("/")
    def index():
        return Response("bear", background=after)

    assert app.test_client().get("/").body == b"bear"
    assert completed == ["/"]


@pytest.mark.parametrize("failure", [False, True])
def test_request_context_closes_body_and_upload_after_teardown(failure):
    app = Higuma(__name__, static_folder=None)
    opened = []

    @app.teardown_request
    def cleanup(error):
        assert not request.stream.closed
        assert not request.files["file"].stream.closed
        assert isinstance(error, ValueError) is failure

    def exercise():
        with app.test_request_context(
            "/", method="POST", files={"file": ("bear.txt", b"bear")}
        ) as current:
            opened.extend([current.stream, current.files["file"].stream])
            if failure:
                raise ValueError("test failure")

    if failure:
        with pytest.raises(ValueError, match="test failure"):
            exercise()
    else:
        exercise()
    assert all(stream.closed for stream in opened)
    assert not has_request_context()


def test_nested_context_for_same_request_closes_only_at_outer_exit():
    app = Higuma(__name__, static_folder=None)
    current = Request({"body": b"bear"})
    with app.request_context(current):
        with app.request_context(current):
            stream = current.stream
        assert not stream.closed and request._get_current() is current
    assert stream.closed and not has_request_context()


def test_request_close_failure_still_restores_context():
    app = Higuma(__name__, static_folder=None)

    class BrokenRequest(Request):
        def close(self):
            raise ValueError("close failed")

    with pytest.raises(ValueError, match="close failed"), app.request_context(BrokenRequest({})):
        pass
    assert not has_request_context()


def test_completed_custom_stream_is_closed_before_background_once():
    app = Higuma(__name__, static_folder=None)
    calls = []

    class Chunks(io.BytesIO):
        def close(self):
            if not self.closed:
                calls.append("close")
            super().close()

    @app.get("/")
    def index():
        return StreamingResponse(Chunks(b"bear"), background=lambda: calls.append("after"))

    assert app.test_client().get("/").body == b"bear"
    assert calls == ["close", "after"]


def test_background_failure_still_closes_body():
    app = Higuma(__name__, static_folder=None)
    opened = []

    async def after():
        raise ValueError("background failed")

    @app.get("/")
    def index():
        opened.append(request.stream)
        return Response("bear", background=after)

    with pytest.raises(ValueError, match="background failed"):
        app.test_client().get("/")
    assert opened[0].closed and not has_request_context()


@pytest.mark.parametrize("async_handler", [False, True])
@pytest.mark.parametrize("disconnect_at", ["final", "length", "head"])
def test_asgi_completed_response_disconnect_does_not_cancel_background(
    async_handler, disconnect_at
):
    from test_gateways import scope

    app = Higuma(__name__, static_folder=None)
    calls = []

    async def after():
        await asyncio.sleep(0.01)
        calls.append(request.path)

    def index():
        return Response("bear", background=after)

    async def async_index():
        return index()

    app.get("/")(async_index if async_handler else index)

    async def exercise():
        incoming = asyncio.Queue()
        await incoming.put({"type": "http.request", "body": b""})

        async def send(event):
            complete = (disconnect_at == "head" and event["type"] == "http.response.start") or (
                event["type"] == "http.response.body"
                and (
                    (disconnect_at == "length" and event.get("body") == b"bear")
                    or (disconnect_at == "final" and not event.get("more_body", False))
                )
            )
            if complete:
                await incoming.put({"type": "http.disconnect"})
                await asyncio.sleep(0)

        await asyncio.wait_for(
            app.asgi_app(
                scope(method="HEAD" if disconnect_at == "head" else "GET"), incoming.get, send
            ),
            3,
        )

    asyncio.run(exercise())
    assert calls == ["/"]


def test_response_background_attribute_assignment_wraps_callable_and_rejects_non_callable():
    app = Higuma(__name__, static_folder=None)
    completed = []

    with pytest.raises(TypeError, match="background must be callable"):
        Response("bear", background="not-callable")  # type: ignore[arg-type]

    response = Response("bear")
    with pytest.raises(TypeError, match="background must be callable"):
        response.background = 123  # type: ignore[assignment]

    @app.after_request
    def attach_background(current_response):
        async def after():
            await asyncio.sleep(0)
            completed.append(request.path)

        current_response.background = after
        return current_response

    @app.get("/assigned")
    def assigned():
        return Response("bear")

    assert app.test_client().get("/assigned").body == b"bear"
    assert completed == ["/assigned"]


def test_streaming_response_content_length_and_head_across_clients_and_gateways():
    from test_gateways import asgi_request, wsgi_request

    app = Higuma(__name__, static_folder=None)
    events = []

    @app.get("/sized")
    def sized():
        def chunks():
            try:
                yield b"be"
                yield b"ar"
            finally:
                events.append("sized-closed")

        return StreamingResponse(chunks(), content_length=4)

    @app.get("/infinite")
    def infinite():
        class InfiniteStream:
            def __iter__(self):
                while True:
                    events.append("infinite-pulled")
                    yield b"chunk"

            def close(self):
                events.append("infinite-closed")

        return StreamingResponse(InfiniteStream())

    client = app.test_client()
    head_sized = client.head("/sized")
    assert head_sized.body == b""
    assert head_sized.headers["content-length"] == "4"

    get_sized = client.get("/sized")
    assert get_sized.body == b"bear"
    assert get_sized.headers["content-length"] == "4"

    head_infinite = client.head("/infinite")
    assert head_infinite.body == b""
    assert "content-length" not in head_infinite.headers
    assert "infinite-pulled" not in events
    assert "infinite-closed" in events

    wsgi_sized = wsgi_request(app, "/sized")
    assert wsgi_sized["body"] == b"bear"
    assert dict(wsgi_sized["headers"])["content-length"] == "4"

    asgi_events = asyncio.run(asgi_request(app, "/sized"))
    assert dict(asgi_events[0]["headers"])[b"content-length"] == b"4"


@pytest.mark.parametrize("async_mode", [False, True])
def test_replaced_streaming_response_closes_original_iterator(async_mode):
    from higuma import after_this_request

    app = Higuma(__name__, static_folder=None)
    closed = []

    def make_stream(label):
        class Closable:
            def __iter__(self):
                yield b"first"

            async def __aiter__(self):
                yield b"first"

            def close(self):
                closed.append(label)

            async def aclose(self):
                await asyncio.sleep(0)
                closed.append(label)

        return StreamingResponse(Closable())

    def replace_hook(response):
        return Response("replaced")

    @app.get("/replace")
    async def async_replace():
        after_this_request(replace_hook)
        return make_stream("async")

    @app.get("/sync-replace")
    def sync_replace():
        after_this_request(replace_hook)
        return make_stream("sync")

    if async_mode:

        async def exercise():
            async with app.test_async_client() as client:
                result = await client.get("/replace")
                assert result.text == "replaced"

        asyncio.run(exercise())
        assert closed == ["async"]
    else:
        assert app.test_client().get("/sync-replace").text == "replaced"
        assert closed == ["sync"]


def test_dispatch_cancellation_keeps_request_open_for_dependency_and_teardown():
    from typing import Annotated

    from higuma import Depends
    from test_gateways import scope

    app = Higuma(__name__, static_folder=None)
    started = asyncio.Event()
    states = []
    streams = []

    async def dep():
        try:
            yield "ok"
        finally:
            await asyncio.sleep(0)
            states.append(("dep", request.stream.closed))

    @app.teardown_request
    async def teardown(error):
        await asyncio.sleep(0)
        states.append(
            ("teardown", request.stream.closed, isinstance(error, asyncio.CancelledError))
        )

    @app.post("/cancel")
    def cancel_route(value: Annotated[str, Depends(dep)]):
        streams.append(request.stream)

        async def wait_forever():
            started.set()
            await asyncio.sleep(60)

        from higuma.execution import resolve_awaitable

        resolve_awaitable(wait_forever())
        return value

    async def exercise():
        incoming = asyncio.Queue()
        await incoming.put({"type": "http.request", "body": b"payload"})

        async def send(event):
            pass

        task = asyncio.create_task(
            app.asgi_app(scope("/cancel", method="POST"), incoming.get, send)
        )
        await asyncio.wait_for(started.wait(), 2)
        await incoming.put({"type": "http.disconnect"})
        await asyncio.wait_for(task, 2)

    asyncio.run(exercise())
    assert states == [("dep", False), ("teardown", False, True)]
    assert streams and streams[0].closed


def test_asgi_send_error_with_concurrent_disconnect_completes_async_cleanup():
    from test_gateways import scope

    app = Higuma(__name__, static_folder=None)
    events = []

    async def after():
        await asyncio.sleep(0.01)
        events.append("background")

    @app.get("/")
    def index():
        async def chunks():
            try:
                yield b"first"
                yield b"second"
            finally:
                await asyncio.sleep(0.01)
                events.append("stream-closed")

        return StreamingResponse(chunks(), background=after)

    async def exercise():
        incoming = asyncio.Queue()
        await incoming.put({"type": "http.request", "body": b""})

        async def send(event):
            if event["type"] == "http.response.body":
                await incoming.put({"type": "http.disconnect"})
                await asyncio.sleep(0)
                raise OSError("peer gone")

        await asyncio.wait_for(app.asgi_app(scope(), incoming.get, send), 3)

    asyncio.run(exercise())
    assert events == ["stream-closed", "background"]


def test_asgi_websocket_disconnect_allows_async_cleanup_and_background():
    from higuma import BackgroundTasks, WebSocketDisconnect
    from test_gateways import scope

    app = Higuma(__name__, static_folder=None)
    events = []

    async def after_ws():
        await asyncio.sleep(0.01)
        events.append("background")

    @app.teardown_request
    async def teardown(error):
        await asyncio.sleep(0)
        events.append(("teardown", request.endpoint, error))

    @app.websocket("/ws")
    async def ws_endpoint(ws):
        request.state["_higuma_background_tasks"] = tasks = BackgroundTasks()
        tasks.add_task(after_ws)
        try:
            await ws.areceive_text()
        except WebSocketDisconnect as exc:
            await asyncio.sleep(0.01)
            events.append(("disconnect", exc.code))

    async def exercise():
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        task = asyncio.create_task(
            app.asgi_app(scope("/ws", type="websocket"), incoming.get, outgoing.put)
        )
        await incoming.put({"type": "websocket.connect"})
        assert (await asyncio.wait_for(outgoing.get(), 2))["type"] == "websocket.accept"
        await incoming.put({"type": "websocket.disconnect", "code": 1001})
        await asyncio.wait_for(task, 2)

    asyncio.run(exercise())
    assert events == [
        ("teardown", "ws_endpoint", None),
        ("disconnect", 1001),
        ("teardown", "ws_endpoint", None),
        "background",
    ]
