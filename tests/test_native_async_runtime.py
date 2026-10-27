import asyncio
from contextvars import ContextVar
from typing import Annotated

import pytest
from higuma import BackgroundTasks, Depends, Higuma, StreamingResponse, g, request


def test_native_async_http_reuses_loop_preserves_task_context_and_closes_request_scope():
    app = Higuma(__name__, static_folder=None)
    marker = ContextVar("native_dependency_marker", default=None)
    loops, tasks, cleaned, requests = [], [], [], []

    async def dependency():
        token = marker.set("resource")
        tasks.append(asyncio.current_task())
        try:
            yield "resource"
        finally:
            tasks.append(asyncio.current_task())
            marker.reset(token)
            cleaned.append("dependency")

    @app.get("/")
    async def index(value: Annotated[str, Depends(dependency)], background: BackgroundTasks):
        tasks.append(asyncio.current_task())
        loops.append(asyncio.get_running_loop())
        assert marker.get() == value
        requests.append(request._get_current())
        g.owner = "bear"

        async def after():
            assert g.owner == "bear"
            loops.append(asyncio.get_running_loop())
            cleaned.append("background")

        background.add_task(after)
        return {"value": value}

    client = app.test_client()
    for _ in range(3):
        assert client.get("/").get_json() == {"value": "resource"}
    assert len(set(loops)) == 1
    assert all(tasks[index] is tasks[index + 1] is tasks[index + 2] for index in range(0, 9, 3))
    assert cleaned == ["background", "dependency"] * 3
    assert all("_higuma_native_bridge" not in item.state for item in requests)
    assert marker.get() is None


def test_native_lazy_async_stream_and_reentrant_async_middleware_remain_usable():
    app = Higuma(__name__, static_folder=None)
    closed, saved = [], []

    @app.get("/stream")
    def stream():
        saved.append(request._get_current())

        async def chunks():
            try:
                yield "bear"
                await asyncio.sleep(0)
            finally:
                closed.append(True)

        return StreamingResponse(chunks())

    assert app.test_client().get("/stream").body == b"bear"
    assert closed == [True] and "_higuma_native_bridge" not in saved[0].state

    @app.middleware
    async def outer(current, call_next):
        await asyncio.sleep(0)
        return call_next(current)

    @app.get("/async")
    async def asynchronous():
        await asyncio.sleep(0)
        return {"ok": True}

    assert app.test_client().get("/async").get_json() == {"ok": True}


def test_native_test_client_failed_stream_closes_await_scope_and_dependencies():
    app = Higuma(__name__, static_folder=None)
    closed, saved = [], []

    async def dependency():
        try:
            yield "resource"
        finally:
            closed.append("dependency")

    @app.get("/")
    def broken(value: Annotated[str, Depends(dependency)]):
        saved.append(request._get_current())

        async def chunks():
            yield value
            raise ValueError("broken stream")

        return StreamingResponse(chunks())

    with pytest.raises(ValueError, match="broken stream"):
        app.test_client().get("/")
    assert closed == ["dependency"] and "_higuma_native_bridge" not in saved[0].state
