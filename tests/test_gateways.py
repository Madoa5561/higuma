import asyncio
import io
import json
from contextlib import asynccontextmanager
from typing import Annotated
from wsgiref.util import setup_testing_defaults
from wsgiref.validate import validator

import pytest
from higuma import (
    BackgroundTasks,
    Depends,
    Higuma,
    Response,
    SessionMiddleware,
    StreamingResponse,
    g,
    request,
    session,
    url_for,
)


def scope(path="/", **updates):
    result = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.5"},
        "http_version": "1.1",
        "scheme": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"example.test")],
        "client": ("127.0.0.1", 1234),
        "server": ("example.test", 80),
    }
    result.update(updates)
    return result


async def asgi_request(app, path="/", *, body=b"", events=None, **updates):
    incoming = asyncio.Queue()
    for event in events or [{"type": "http.request", "body": body}]:
        incoming.put_nowait(event)
    sent = []

    async def send(event):
        sent.append(event)

    await asyncio.wait_for(app.asgi_app(scope(path, **updates), incoming.get, send), 3)
    return sent


def content(events):
    return b"".join(
        event.get("body", b"") for event in events if event["type"] == "http.response.body"
    )


def wsgi_request(app, path="/", *, body=b"", method="GET", validate_environ=True, **updates):
    environ = {}
    setup_testing_defaults(environ)
    environ.update(PATH_INFO=path, REQUEST_METHOD=method, QUERY_STRING="", HTTP_HOST="example.test")
    environ.update({"wsgi.input": io.BytesIO(body), "CONTENT_LENGTH": str(len(body))})
    environ.update(updates)
    result = {}

    def start_response(status, headers, exc_info=None):
        result.update(status=int(status[:3]), headers=headers)

    application = validator(app.wsgi_app) if validate_environ else app.wsgi_app
    iterable = application(environ, start_response)
    try:
        result["body"] = b"".join(iterable)
    finally:
        iterable.close()
    return result


@pytest.mark.parametrize("transport", ["asgi", "wsgi"])
def test_gateway_routing_cookies_headers_and_bodyless_responses(transport):
    app = Higuma(__name__, static_folder=None)

    @app.get("/items/<int:number>")
    def item(number):
        result = Response(
            str(number), headers={"Connection": "X-Hop", "X-Hop": "drop", "Content-Length": "999"}
        )
        result.set_cookie("one", "1")
        result.set_cookie("two", "2")
        return result

    @app.get("/empty")
    def empty():
        return "must-not-send", 204

    def call(path, method="GET"):
        if transport == "wsgi":
            result = wsgi_request(app, path, method=method)
            return result["status"], result["headers"], result["body"]
        events = asyncio.run(asgi_request(app, path, method=method))
        return (
            events[0]["status"],
            [(k.decode(), v.decode()) for k, v in events[0]["headers"]],
            content(events),
        )

    status, headers, body = call("/items/7")
    assert status == 200 and body == b"7"
    assert dict(headers)["content-length"] == "1"
    assert "connection" not in dict(headers) and "x-hop" not in dict(headers)
    assert len([item for item in headers if item[0] == "set-cookie"]) == 2
    status, headers, body = call("/items/7", "HEAD")
    assert status == 200 and body == b"" and dict(headers)["content-length"] == "1"
    assert call("/items/7", "POST")[0] == 405
    assert call("/items/7", "OPTIONS")[0] == 204
    assert call("/missing")[0] == 404
    assert call("/empty")[2] == b""


def test_asgi_body_limit_chunked_json_and_duplicate_cookie_headers():
    app = Higuma(__name__, static_folder=None, max_content_length=16)

    @app.post("/")
    def index():
        return {
            "body": request.json,
            "cookies": dict(request.cookies),
            "raw": len(request.raw_headers),
        }

    events = asyncio.run(
        asgi_request(
            app,
            method="POST",
            headers=[
                (b"content-type", b"application/json"),
                (b"cookie", b"a=1"),
                (b"cookie", b"b=2"),
            ],
            events=[
                {"type": "http.request", "body": b'{"a":', "more_body": True},
                {"type": "http.request", "body": b"1}"},
            ],
        )
    )
    assert json.loads(content(events)) == {
        "body": {"a": 1},
        "cookies": {"a": "1", "b": "2"},
        "raw": 4,
    }
    events = asyncio.run(asgi_request(app, body=b"x" * 17, method="POST"))
    assert events[0]["status"] == 413


@pytest.mark.parametrize("transport", ["asgi", "wsgi"])
def test_gateway_root_path_urls_and_unicode(transport):
    app = Higuma(__name__, static_folder=None)

    @app.get("/bear/<name>")
    def bear(name):
        return {"name": name, "url": request.url, "link": url_for("bear", name="日本")}

    if transport == "asgi":
        events = asyncio.run(
            asgi_request(
                app, "/api/bear/日本", raw_path=b"/api/bear/%E6%97%A5%E6%9C%AC", root_path="/api"
            )
        )
        result = json.loads(content(events))
    else:
        result = json.loads(
            wsgi_request(app, "/bear/日本".encode().decode("latin-1"), SCRIPT_NAME="/api")["body"]
        )
    assert result == {
        "name": "日本",
        "url": "http://example.test/api/bear/%E6%97%A5%E6%9C%AC",
        "link": "/api/bear/%E6%97%A5%E6%9C%AC",
    }


def test_asgi_lifespan_dependencies_stream_and_background_use_server_loop():
    loops = []
    cleanup = []

    @asynccontextmanager
    async def lifespan(app):
        loops.append(asyncio.get_running_loop())
        yield {"ready": True}
        loops.append(asyncio.get_running_loop())

    app = Higuma(__name__, static_folder=None, lifespan=lifespan)

    async def dependency():
        loops.append(asyncio.get_running_loop())
        try:
            yield "resource"
        finally:
            loops.append(asyncio.get_running_loop())
            cleanup.append("dependency")

    @app.get("/")
    async def index(tasks: BackgroundTasks, resource: Annotated[str, Depends(dependency)]):
        assert app.state["ready"] and resource == "resource"
        assert request.scope["state"]["ready"]
        loops.append(asyncio.get_running_loop())
        g.owner = request.path

        async def background():
            assert g.owner == "/"
            loops.append(asyncio.get_running_loop())
            cleanup.append("background")

        tasks.add_task(background)

        async def stream():
            loops.append(asyncio.get_running_loop())
            assert g.owner == "/"
            yield "bear"
            await asyncio.sleep(0)
            yield "!"

        return StreamingResponse(stream())

    async def exercise():
        incoming = asyncio.Queue()
        outgoing = asyncio.Queue()
        shared_state = {}
        task = asyncio.create_task(
            app.asgi_app({"type": "lifespan", "state": shared_state}, incoming.get, outgoing.put)
        )
        await incoming.put({"type": "lifespan.startup"})
        assert (await outgoing.get())["type"] == "lifespan.startup.complete"
        assert shared_state == {"ready": True}
        assert content(await asgi_request(app, state=shared_state.copy())) == b"bear!"
        await incoming.put({"type": "lifespan.shutdown"})
        assert (await outgoing.get())["type"] == "lifespan.shutdown.complete"
        await task
        assert all(loop is asyncio.get_running_loop() for loop in loops)

    asyncio.run(asyncio.wait_for(exercise(), 5))
    assert cleanup == ["background", "dependency"]


def test_asgi_disconnect_cancels_async_view_and_cleans_dependency():
    async def exercise():
        started = asyncio.Event()
        cleaned = asyncio.Event()
        incoming = asyncio.Queue()
        incoming.put_nowait({"type": "http.request", "body": b""})
        app = Higuma(__name__, static_folder=None)

        async def dependency():
            try:
                yield None
            finally:
                cleaned.set()

        @app.get("/")
        async def index(value: Annotated[None, Depends(dependency)]):
            started.set()
            await asyncio.sleep(60)

        async def send(event):
            pass

        task = asyncio.create_task(app.asgi_app(scope(), incoming.get, send))
        await started.wait()
        await incoming.put({"type": "http.disconnect"})
        await asyncio.wait_for(task, 2)
        assert cleaned.is_set()

    asyncio.run(exercise())


@pytest.mark.parametrize("transport", ["asgi", "wsgi"])
def test_gateway_files_templates_sessions_and_stream_cleanup(tmp_path, transport):
    (tmp_path / "bear.txt").write_text("abcdef", encoding="utf-8")
    (tmp_path / "bear.html").write_text("Hello {{ name }}", encoding="utf-8")
    app = Higuma(__name__, static_folder=None, template_folder=str(tmp_path))
    app.add_middleware(SessionMiddleware, "gateway-tests-secret-at-least-32-bytes")
    closed = []

    @app.get("/file")
    def file():
        from higuma import FileResponse

        return FileResponse(tmp_path / "bear.txt")

    @app.get("/template")
    def template():
        return app.render_template("bear.html", name="higuma")

    @app.get("/session")
    def saved():
        session["saved"] = True
        return "saved"

    @app.get("/stream")
    def stream():
        def chunks():
            try:
                yield "a"
                yield "b"
            finally:
                closed.append(True)

        return StreamingResponse(chunks())

    if transport == "wsgi":
        assert wsgi_request(app, "/file", HTTP_RANGE="bytes=1-3")["body"] == b"bcd"
        assert wsgi_request(app, "/template")["body"] == b"Hello higuma"
        assert any(key == "set-cookie" for key, _ in wsgi_request(app, "/session")["headers"])
        assert wsgi_request(app, "/stream")["body"] == b"ab"
    else:
        assert (
            content(asyncio.run(asgi_request(app, "/file", headers=[(b"range", b"bytes=1-3")])))
            == b"bcd"
        )
        assert content(asyncio.run(asgi_request(app, "/template"))) == b"Hello higuma"
        assert any(
            key == b"set-cookie"
            for key, _ in asyncio.run(asgi_request(app, "/session"))[0]["headers"]
        )
        assert content(asyncio.run(asgi_request(app, "/stream"))) == b"ab"
    assert closed == [True]


@pytest.mark.parametrize("async_handler", [False, True])
def test_asgi_websocket_echo_and_disconnect(async_handler):
    from higuma import WebSocketDisconnect

    async def exercise():
        app = Higuma(__name__, static_folder=None)
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        cleaned = []

        def sync_echo(ws):
            try:
                while True:
                    ws.send_json({"echo": ws.receive_json()})
            except WebSocketDisconnect:
                pass
            finally:
                cleaned.append(True)

        async def async_echo(ws):
            assert asyncio.get_running_loop() is owner
            try:
                while True:
                    await ws.asend_json({"echo": await ws.areceive_json()})
            except WebSocketDisconnect:
                pass
            finally:
                cleaned.append(True)

        owner = asyncio.get_running_loop()
        app.websocket("/ws")(async_echo if async_handler else sync_echo)
        task = asyncio.create_task(
            app.asgi_app(scope("/ws", type="websocket"), incoming.get, outgoing.put)
        )
        await incoming.put({"type": "websocket.connect"})
        assert (await asyncio.wait_for(outgoing.get(), 2))["type"] == "websocket.accept"
        await incoming.put({"type": "websocket.receive", "text": '{"bear":"higuma"}'})
        event = await asyncio.wait_for(outgoing.get(), 2)
        assert event["type"] == "websocket.send"
        assert json.loads(event["text"]) == {"echo": {"bear": "higuma"}}
        await incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(task, 2)
        assert cleaned == [True]
        assert outgoing.empty()

    asyncio.run(exercise())


@pytest.mark.parametrize("origins", [None, ("https://allowed.example",)])
def test_asgi_websocket_origin_denial_before_upgrade(origins):
    app = Higuma(__name__, static_folder=None)
    ran = []

    @app.websocket("/ws", allowed_origins=origins)
    def echo(ws):
        ran.append(True)

    async def exercise():
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        await incoming.put({"type": "websocket.connect"})
        await app.asgi_app(
            scope(
                "/ws",
                type="websocket",
                headers=[(b"host", b"example.test"), (b"origin", b"https://evil.example")],
            ),
            incoming.get,
            outgoing.put,
        )
        assert (await outgoing.get())["type"] == "websocket.close"

    asyncio.run(exercise())
    assert not ran


def test_asgi_stream_backpressure_and_disconnect_closes_generator():
    async def exercise():
        app = Higuma(__name__, static_folder=None)
        incoming = asyncio.Queue()
        await incoming.put({"type": "http.request"})
        blocked = asyncio.Event()
        produced = []
        closed = []

        @app.get("/")
        def index():
            async def stream():
                try:
                    for index in range(20):
                        produced.append(index)
                        yield b"chunk"
                finally:
                    closed.append(True)

            return StreamingResponse(stream())

        async def send(event):
            if event["type"] == "http.response.body" and event.get("more_body"):
                blocked.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(app.asgi_app(scope(), incoming.get, send))
        await asyncio.wait_for(blocked.wait(), 2)
        await asyncio.sleep(0.01)
        assert produced == [0]
        await incoming.put({"type": "http.disconnect"})
        await asyncio.wait_for(task, 2)
        assert closed == [True]

    asyncio.run(exercise())


def test_wsgi_early_close_closes_stream_and_runs_background_once():
    from higuma import BackgroundTask

    app = Higuma(__name__, static_folder=None)
    closed = []

    @app.get("/")
    def index():
        def stream():
            try:
                yield b"one"
                yield b"two"
            finally:
                closed.append("stream")

        return StreamingResponse(stream(), background=BackgroundTask(closed.append, "background"))

    environ = {}
    setup_testing_defaults(environ)
    result = app.wsgi_app(environ, lambda status, headers: None)
    iterator = iter(result)
    assert next(iterator) == b"one"
    result.close()
    result.close()
    iterator.close()
    assert closed == ["stream", "background"]


def test_asgi_executor_is_separate_and_concurrent_contexts_are_isolated():
    from concurrent.futures import ThreadPoolExecutor

    async def exercise():
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
        app = Higuma(__name__, static_folder=None)
        app.config["ASGI_MAX_CONCURRENCY"] = 2

        @app.get("/<int:number>")
        async def index(number):
            g.number = number
            await asyncio.to_thread(lambda: None)
            await asyncio.sleep(0.001)
            return {"number": g.number, "path": request.path}

        results = await asyncio.gather(*(asgi_request(app, f"/{number}") for number in range(12)))
        assert [json.loads(content(result)) for result in results] == [
            {"number": number, "path": f"/{number}"} for number in range(12)
        ]

    asyncio.run(exercise())


@pytest.mark.parametrize("phase", ["startup", "shutdown"])
def test_asgi_lifespan_failures_are_reported_without_secret_details(phase):
    app = Higuma(__name__, static_folder=None)

    async def fail():
        raise ValueError("private-test-value")

    getattr(app, "on_" + phase)(fail)

    async def exercise():
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        await incoming.put({"type": "lifespan.startup"})
        if phase == "shutdown":
            await incoming.put({"type": "lifespan.shutdown"})
        await app.asgi_app({"type": "lifespan"}, incoming.get, outgoing.put)
        events = []
        while not outgoing.empty():
            events.append(outgoing.get_nowait())
        assert events[-1]["type"] == f"lifespan.{phase}.failed"
        assert "private-test-value" not in str(events)

    asyncio.run(exercise())


@pytest.mark.parametrize("transport", ["asgi", "wsgi"])
def test_gateway_subpath_openapi_and_nested_flask(transport):
    flask = pytest.importorskip("flask")
    legacy = flask.Flask(__name__)

    @legacy.get("/hello")
    def hello():
        return {"link": flask.url_for("hello")}

    app = Higuma(__name__, static_folder=None)
    app.mount_wsgi("/legacy", legacy)

    def call(path):
        if transport == "asgi":
            return content(asyncio.run(asgi_request(app, "/outer" + path, root_path="/outer")))
        return wsgi_request(app, path, SCRIPT_NAME="/outer")["body"]

    assert json.loads(call("/legacy/hello")) == {"link": "/outer/legacy/hello"}
    assert b'"/outer/openapi.json"' in call("/docs")
    assert json.loads(call("/openapi.json"))["servers"] == [{"url": "/outer"}]


@pytest.mark.parametrize(
    "body,length,terminated,status",
    [
        (b"toolarge", "8", False, 413),
        (b"toolarge", "", True, 413),
        (b"x", "3", False, 400),
        (b"x", "-1", False, 400),
    ],
)
def test_wsgi_input_limits_and_incomplete_bodies(body, length, terminated, status):
    app = Higuma(__name__, static_folder=None, max_content_length=4)
    result = wsgi_request(
        app,
        body=body,
        CONTENT_LENGTH=length,
        validate_environ=length != "-1",
        **{"wsgi.input_terminated": terminated},
    )
    assert result["status"] == status


def test_wsgi_bytes_and_none_responses_have_valid_content_type():
    app = Higuma(__name__, static_folder=None)
    app.get("/bytes", endpoint="bytes")(lambda: b"bear")
    app.get("/none", endpoint="none")(lambda: None)
    assert wsgi_request(app, "/bytes")["body"] == b"bear"
    assert wsgi_request(app, "/none")["body"] == b""


def test_asgi_send_disconnect_closes_stream_and_background():
    from higuma import BackgroundTask

    app = Higuma(__name__, static_folder=None)
    cleaned = []

    @app.get("/")
    def index():
        def stream():
            try:
                yield b"bear"
                yield b"next"
            finally:
                cleaned.append("stream")

        return StreamingResponse(stream(), background=BackgroundTask(cleaned.append, "background"))

    async def exercise():
        incoming = asyncio.Queue()
        await incoming.put({"type": "http.request"})

        async def send(event):
            if event["type"] == "http.response.body":
                raise OSError("peer gone")

        await app.asgi_app(scope(), incoming.get, send)

    asyncio.run(exercise())
    assert cleaned == ["stream", "background"]


def test_asgi_websocket_message_limit_closes_before_delivery():
    async def exercise():
        app = Higuma(__name__, static_folder=None)
        app.config["ASGI_WEBSOCKET_MAX_MESSAGE_SIZE"] = 4
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        delivered = []

        @app.websocket("/ws")
        async def socket(ws):
            delivered.append(await ws.areceive_text())

        task = asyncio.create_task(
            app.asgi_app(scope("/ws", type="websocket"), incoming.get, outgoing.put)
        )
        await incoming.put({"type": "websocket.connect"})
        assert (await outgoing.get())["type"] == "websocket.accept"
        await incoming.put({"type": "websocket.receive", "text": "too large"})
        event = await asyncio.wait_for(outgoing.get(), 2)
        assert event == {"type": "websocket.close", "code": 1009}
        await asyncio.wait_for(task, 2)
        assert not delivered

    asyncio.run(exercise())


def test_asgi_async_dependency_contextvar_is_visible_and_resets_in_same_task():
    from contextvars import ContextVar

    marker = ContextVar("gateway_dependency_marker", default=None)
    tasks = []
    app = Higuma(__name__, static_folder=None)

    async def dependency():
        token = marker.set("transaction")
        tasks.append(asyncio.current_task())
        try:
            yield "transaction"
        finally:
            tasks.append(asyncio.current_task())
            marker.reset(token)

    @app.get("/")
    async def index(value: Annotated[str, Depends(dependency)]):
        tasks.append(asyncio.current_task())
        assert marker.get() == value
        return {"value": value}

    assert json.loads(content(asyncio.run(asgi_request(app)))) == {"value": "transaction"}
    assert len(tasks) == 3 and all(task is tasks[0] for task in tasks)
    assert marker.get() is None


def test_gateway_generated_media_type_cannot_inject_headers():
    from higuma.gateways import _headers

    with pytest.raises(ValueError, match="invalid HTTP header"):
        _headers(Response(b"body", media_type="text/plain\r\nX-Injected: yes"), 4)


@pytest.mark.parametrize("transport", ["asgi", "wsgi"])
def test_gateway_unicode_root_path_is_encoded_once(transport):
    app = Higuma(__name__, static_folder=None)

    @app.get("/")
    def index():
        return {"root": request.root_path, "url": request.url, "link": url_for("index")}

    if transport == "asgi":
        result = json.loads(
            content(
                asyncio.run(
                    asgi_request(app, "/管理/", raw_path=b"/%E7%AE%A1%E7%90%86/", root_path="/管理")
                )
            )
        )
    else:
        result = json.loads(
            wsgi_request(app, SCRIPT_NAME="/管理".encode().decode("latin-1"))["body"]
        )
    assert result == {
        "root": "/管理",
        "url": "http://example.test/%E7%AE%A1%E7%90%86/",
        "link": "/%E7%AE%A1%E7%90%86/",
    }


@pytest.mark.parametrize("spec_version", ["2.0", "2.1"])
def test_asgi_websocket_handshake_cookies_require_supported_spec(spec_version):
    app = Higuma(__name__, static_folder=None)
    app.add_middleware(SessionMiddleware, "gateway-cookie-test-secret-at-least-32-bytes")
    called = []

    @app.middleware
    def save_session(current_request, call_next):
        session["handshake"] = True
        return call_next(current_request)

    @app.websocket("/ws")
    def socket(ws):
        called.append(True)

    async def exercise():
        incoming = asyncio.Queue()
        incoming.put_nowait({"type": "websocket.connect"})
        events = []

        async def send(event):
            events.append(event)

        await asyncio.wait_for(
            app.asgi_app(
                scope(
                    "/ws", type="websocket", asgi={"version": "3.0", "spec_version": spec_version}
                ),
                incoming.get,
                send,
            ),
            3,
        )
        return events

    events = asyncio.run(exercise())
    if spec_version == "2.0":
        assert events == [{"type": "websocket.close", "code": 1008}]
        assert not called
    else:
        assert events[0]["type"] == "websocket.accept"
        assert any(key == b"set-cookie" for key, _ in events[0]["headers"])
        assert called == [True]


def test_asgi_websocket_allowed_origins_normalizes_trailing_slash():
    app = Higuma(__name__, static_folder=None)
    connected = []

    @app.websocket("/ws", allowed_origins=("https://app.example/",))
    def ws_handler(ws):
        connected.append(True)

    async def exercise():
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        await incoming.put({"type": "websocket.connect"})
        await incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(
            app.asgi_app(
                scope(
                    "/ws",
                    type="websocket",
                    headers=[
                        (b"host", b"example.test"),
                        (b"origin", b"https://app.example"),
                    ],
                ),
                incoming.get,
                outgoing.put,
            ),
            3,
        )
        return await outgoing.get()

    assert asyncio.run(exercise())["type"] == "websocket.accept"
    assert connected == [True]


def test_asgi_bad_request_head_and_mounted_asgi_malformed_content_length():
    app = Higuma(__name__, static_folder=None)

    async def sub_asgi(asgi_scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-length", b"bogus"), (b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    app.mount_asgi("/sub", sub_asgi)

    events = asyncio.run(
        asgi_request(
            app,
            "/sub/hello",
            headers=[(b"host", b"example.test:notaport")],
        )
    )
    assert events[0]["status"] == 200
    assert content(events) == b"ok"

    bad_events = asyncio.run(
        asgi_request(
            app,
            "/bad",
            method="HEAD",
            raw_path=b"/\xff",
        )
    )
    assert bad_events[0]["status"] == 400
    assert any(key == b"content-type" for key, _ in bad_events[0]["headers"])
    assert content(bad_events) == b""


def test_wsgi_body_seek_failure_closes_file_and_background_runs_once(tmp_path):
    from unittest.mock import patch

    from higuma import BackgroundTask, FileResponse
    from higuma.gateways import _Body

    sample = tmp_path / "sample.bin"
    sample.write_bytes(b"hello")
    closed_handles = []
    real_open = open

    class FailingSeekFile:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def seek(self, offset):
            raise OSError("seek failed")

        def close(self):
            closed_handles.append(True)
            self._wrapped.close()

    def fake_open(path, mode="r", *args, **kwargs):
        return FailingSeekFile(real_open(path, mode, *args, **kwargs))

    with (
        patch("builtins.open", side_effect=fake_open),
        pytest.raises(OSError, match="seek failed"),
    ):
        _Body(FileResponse(sample), "GET")
    assert closed_handles == [True]

    app = Higuma(__name__, static_folder=None)
    ran = []

    @app.get("/missing")
    def missing():
        response = FileResponse(sample)
        response.background = BackgroundTask(ran.append, "bg")
        sample.unlink()
        return response

    result = wsgi_request(app, "/missing")
    assert result["status"] == 500
    assert ran == ["bg"]


def test_root_mount_wsgi_and_asgi_route_root_and_subpaths():
    wsgi_host = Higuma(__name__, static_folder=None, docs_url=None, openapi_url=None)

    def wsgi_app(environ, start_response):
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [environ["PATH_INFO"].encode("utf-8")]

    wsgi_host.mount_wsgi("/", wsgi_app)
    wsgi_client = wsgi_host.test_client()
    assert wsgi_client.get("/").text == "/"
    assert wsgi_client.get("/nested/page").text == "/nested/page"

    asgi_host = Higuma(__name__, static_folder=None, docs_url=None, openapi_url=None)

    async def asgi_sub(asgi_scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": asgi_scope["path"].encode("utf-8"),
                "more_body": False,
            }
        )

    asgi_host.mount_asgi("/", asgi_sub)
    asgi_client = asgi_host.test_client()
    assert asgi_client.get("/").text == "/"
    assert asgi_client.get("/nested/page").text == "/nested/page"


def test_asgi_fallback_500_emits_headers_and_suppresses_head_body(tmp_path):
    from higuma import FileResponse

    app = Higuma(__name__, static_folder=None)
    missing = tmp_path / "gone.txt"
    missing.write_text("bye", encoding="utf-8")

    @app.get("/missing")
    def missing_file():
        response = FileResponse(missing)
        missing.unlink()
        return response

    async def exercise():
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        await incoming.put({"type": "http.request"})
        await app.asgi_app(scope("/missing", method="HEAD"), incoming.get, outgoing.put)
        start = await outgoing.get()
        body = await outgoing.get()
        headers = {k.lower(): v for k, v in start["headers"]}
        assert start["status"] == 500
        assert b"content-type" in headers
        assert b"content-length" in headers
        assert body["body"] == b""

    asyncio.run(exercise())


def test_asgi_websocket_rejects_malformed_scope_and_tolerates_bad_spec_version():
    app = Higuma(__name__, static_folder=None)

    @app.websocket("/ws")
    def echo(ws):
        ws.send_text("ok")

    async def exercise():
        # Malformed non-ASCII raw_path closes cleanly with 1002 instead of crashing.
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        await incoming.put({"type": "websocket.connect"})
        bad_scope = scope("/ws", type="websocket")
        bad_scope["raw_path"] = b"/ws/\xff"
        await app.asgi_app(bad_scope, incoming.get, outgoing.put)
        assert await outgoing.get() == {"type": "websocket.close", "code": 1002}

        # Malformed ASGI spec_version falls back to (2, 0) instead of crashing.
        incoming2, outgoing2 = asyncio.Queue(), asyncio.Queue()
        await incoming2.put({"type": "websocket.connect"})
        weird_scope = scope("/ws", type="websocket")
        weird_scope["asgi"] = {"version": "3.0", "spec_version": "2.3-beta"}
        task = asyncio.create_task(app.asgi_app(weird_scope, incoming2.get, outgoing2.put))
        assert (await asyncio.wait_for(outgoing2.get(), 2))["type"] == "websocket.accept"
        assert (await asyncio.wait_for(outgoing2.get(), 2))["text"] == "ok"
        assert (await asyncio.wait_for(outgoing2.get(), 2))["type"] == "websocket.close"
        await asyncio.wait_for(task, 2)

    asyncio.run(exercise())


def test_wsgi_mount_missing_start_response_and_prefix_boundary():
    from higuma.mounts import _mounted_path

    assert _mounted_path("/api", "/api") == "/"
    assert _mounted_path("/api/items", "/api") == "/items"
    assert _mounted_path("/api-v2", "/api") == "/api-v2"

    app = Higuma(__name__, static_folder=None)

    def broken_wsgi(environ, start_response):
        return [b"missing start_response"]

    app.mount_wsgi("/broken", broken_wsgi)
    response = app.test_client().get("/broken/")
    assert response.status_code == 500


def test_asgi_and_test_client_validate_limits():
    app = Higuma(__name__, static_folder=None)
    app.config["ASGI_MAX_CONCURRENCY"] = True

    async def exercise():
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        with pytest.raises(ValueError, match="ASGI_MAX_CONCURRENCY"):
            await app.asgi_app(scope("/"), incoming.get, outgoing.put)

    asyncio.run(exercise())

    with pytest.raises(ValueError, match="timeout"):
        app.test_async_client(timeout=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="timeout"):
        app.test_async_client(timeout=float("nan"))
    with pytest.raises(ValueError, match="max_redirects"):
        app.test_client().get("/", max_redirects=True)  # type: ignore[arg-type]


def test_asgi_websocket_origin_parity_raw_path_fallback_and_close_validation():
    from higuma.gateways import _scope_request

    # Empty raw_path falls back to scope["path"]
    raw_fallback = _scope_request({"type": "http", "path": "/items", "raw_path": b""})
    assert raw_fallback["path"] == "/items"

    app = Higuma(__name__, static_folder=None)

    @app.websocket("/ws-same")
    def ws_same(ws):
        ws.send_text("ok")

    @app.websocket("/ws-explicit", allowed_origins=("https://app.example",))
    def ws_explicit(ws):
        ws.send_text("ok")

    @app.websocket("/ws-star", allowed_origins=("*",))
    def ws_star(ws):
        ws.send_text("ok")

    @app.post("/async-created-headers", status_code=201)
    async def async_created_headers():
        await asyncio.sleep(0)
        return {"ok": True}, {"x-async": "1"}

    @app.websocket("/ws-bad-close")
    def ws_bad_close(ws):
        with pytest.raises(ValueError, match="close code"):
            ws.close(3000.5)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="string"):
            ws.close(1000, 123)  # type: ignore[arg-type]
        ws.send_text("validated")

    async def exercise():
        # Empty Origin header is rejected on same-origin and explicit allowed_origins routes
        for path in ("/ws-same", "/ws-explicit"):
            incoming, outgoing = asyncio.Queue(), asyncio.Queue()
            await incoming.put({"type": "websocket.connect"})
            ws_scope = {
                "type": "websocket",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "scheme": "ws",
                "path": path,
                "headers": [(b"host", b"localhost"), (b"origin", b"")],
            }
            await app.asgi_app(ws_scope, incoming.get, outgoing.put)
            assert (await outgoing.get())["type"] == "websocket.close"

        # Duplicate Origin headers are rejected even with allowed_origins=("*",)
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        await incoming.put({"type": "websocket.connect"})
        dup_scope = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "scheme": "ws",
            "path": "/ws-star",
            "headers": [
                (b"host", b"localhost"),
                (b"origin", b"https://a.example"),
                (b"origin", b"https://b.example"),
            ],
        }
        await app.asgi_app(dup_scope, incoming.get, outgoing.put)
        assert (await outgoing.get())["type"] == "websocket.close"

        # Close code float and non-str reason validation on ASGI WebSocket + async fast-path status_code
        async with app.test_async_client() as client:
            created = await client.post("/async-created-headers")
            assert created.status_code == 201
            assert created.headers["x-async"] == "1"
            async with client.websocket_connect("/ws-bad-close") as ws:
                assert await ws.receive_text() == "validated"

        # Peer disconnecting concurrently during ASGI send/close must not crash teardown
        from threading import Event

        from higuma import WebSocketDisconnect
        from higuma.gateways import _ASGIWebSocket

        async def broken_send(event):
            raise OSError("peer already disconnected")

        ws_session = _ASGIWebSocket(None, asyncio.Queue(), broken_send, Event(), {})  # type: ignore[arg-type]
        await ws_session.aclose(1000, "")
        assert ws_session.closed

        ws_send = _ASGIWebSocket(None, asyncio.Queue(), broken_send, Event(), {})  # type: ignore[arg-type]
        with pytest.raises(WebSocketDisconnect) as caught:
            await ws_send.asend_text("late")
        assert caught.value.code == 1006
        assert ws_send.closed

    asyncio.run(exercise())
