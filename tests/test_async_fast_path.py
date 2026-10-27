import asyncio
import threading
from typing import Annotated

from higuma import (
    BackgroundTasks,
    Depends,
    Higuma,
    StreamingResponse,
    after_this_request,
    g,
    request,
)


def test_async_fast_path_and_configurable_compatibility_path():
    app = Higuma(__name__, static_folder=None)
    observed = []

    @app.get("/value/<int:number>", status_code=201)
    async def value(number: int):
        g.value = number
        observed.append((threading.get_ident(), request.state.get("_higuma_async_fast_path")))

        @after_this_request
        async def finish(response):
            response.headers["x-value"] = str(g.value)
            return response

        return {"value": number}

    async def exercise():
        async with app.test_async_client() as client:
            loop_thread = threading.get_ident()
            response = await client.get("/value/4")
            assert response.status_code == 201 and response.get_json() == {"value": 4}
            assert response.headers["x-value"] == "4"
            assert observed[-1] == (loop_thread, True)
            app.config["ASGI_ASYNC_FAST_PATH"] = False
            assert (await client.get("/value/5")).status_code == 201
            assert observed[-1][1] is None

    asyncio.run(exercise())


def test_async_fast_path_stream_background_and_sync_hooks_fallback():
    app = Higuma(__name__, static_folder=None)
    closed = []

    @app.get("/stream")
    async def stream():
        loop = asyncio.get_running_loop()

        async def chunks():
            try:
                assert asyncio.get_running_loop() is loop
                yield "bear"
            finally:
                closed.append("stream")

        return StreamingResponse(chunks())

    async def dependency():
        yield "resource"
        closed.append("dependency")

    @app.get("/dependency")
    async def resource(value: Annotated[str, Depends(dependency)], background: BackgroundTasks):
        assert not request.state.get("_higuma_async_fast_path")
        background.add_task(lambda: closed.append("background"))
        return value

    async def exercise():
        async with app.test_async_client() as client:
            assert (await client.get("/stream")).body == b"bear"
            assert (await client.get("/dependency")).body == b"resource"
            app.before_request(lambda: closed.append("hook"))
            await client.get("/stream")

    asyncio.run(exercise())
    assert closed == ["stream", "background", "dependency", "hook", "stream"]


def test_fast_path_background_task_stays_on_server_loop_and_runs_once():
    from higuma import BackgroundTask, Response

    app = Higuma(__name__, static_folder=None)
    calls = []

    @app.get("/")
    async def index():
        loop = asyncio.get_running_loop()

        async def after():
            assert asyncio.get_running_loop() is loop
            calls.append(request.endpoint)

        return Response("bear", background=BackgroundTask(after))

    async def exercise():
        async with app.test_async_client() as client:
            assert (await client.get("/")).body == b"bear"

    asyncio.run(exercise())
    assert calls == ["index"]


def test_fast_path_disconnect_cancels_handler_and_releases_request():
    from test_gateways import scope

    app = Higuma(__name__, static_folder=None)
    started, finished = None, []

    @app.get("/")
    async def wait():
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            finished.append(request._get_current())

    async def exercise():
        nonlocal started
        started = asyncio.Event()
        incoming = asyncio.Queue()
        await incoming.put({"type": "http.request", "body": b""})

        async def send(event):
            raise AssertionError("disconnected handler must not send a response")

        task = asyncio.create_task(app.asgi_app(scope(), incoming.get, send))
        await asyncio.wait_for(started.wait(), 2)
        await incoming.put({"type": "http.disconnect"})
        await asyncio.wait_for(task, 2)

    asyncio.run(exercise())
    assert len(finished) == 1 and finished[0]._body_file.closed


def test_async_file_injection_uses_compatibility_path_for_multipart_disk_io():
    from higuma import File, UploadFile

    app = Higuma(__name__, static_folder=None)
    app.config["UPLOAD_MEMORY_LIMIT"] = 4

    @app.post("/")
    async def upload(value: Annotated[UploadFile, File()]):
        assert not request.state.get("_higuma_async_fast_path")
        return {"size": value.size}

    async def exercise():
        async with app.test_async_client() as client:
            response = await client.post("/", files={"value": ("bear.txt", b"large bear")})
            assert response.get_json() == {"size": 10}

    asyncio.run(exercise())
