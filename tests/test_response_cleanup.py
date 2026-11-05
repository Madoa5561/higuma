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
def test_asgi_completed_response_disconnect_does_not_cancel_background(async_handler):
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
            if event["type"] == "http.response.body" and not event.get("more_body", False):
                await incoming.put({"type": "http.disconnect"})

        await asyncio.wait_for(app.asgi_app(scope(), incoming.get, send), 3)

    asyncio.run(exercise())
    assert calls == ["/"]
