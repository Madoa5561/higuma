import asyncio

import pytest
from higuma import BadRequest, Higuma, Request, RequestEntityTooLarge, request
from higuma.body import BodyBuffer


def test_spooled_request_chunk_reading_lazy_body_and_close():
    storage = BodyBuffer(1024, 4)
    storage.write(b"abcdefgh")
    storage.seek(0)
    current = Request({"body_file": storage, "body_size": storage.size})
    assert current.content_length == 8 and current.body_spooled and current._body is None
    assert current["path"] == "/" and current._body is None
    assert list(current.iter_body(3)) == [b"abc", b"def", b"gh"]
    current.stream.seek(0)

    async def read():
        return [chunk async for chunk in current.aiter_body(5)]

    assert asyncio.run(read()) == [b"abcde", b"fgh"]
    assert current.body == b"abcdefgh"
    current.close()
    assert storage.closed
    with pytest.raises(ValueError):
        list(current.iter_body(0))


def test_multipart_cross_chunk_boundaries_limits_and_file_cleanup():
    boundary = "boundary"
    payload = (
        b"a" * 65530 + b"\r\n--boundaryNOT-A-DELIMITER\r\n--boundary--NOT-A-CLOSE" + b"z" * 70000
    )
    body = (
        b"--boundary\r\nContent-Disposition: form-data; name=title\r\n\r\nbear\r\n"
        b"--boundary\r\nContent-Disposition: form-data; name=file; filename=bear.bin\r\n\r\n"
        + payload
        + b"\r\n--boundary--\r\n"
    )
    current = Request(
        {"body": body, "headers": {"Content-Type": "multipart/form-data; boundary=" + boundary}}
    )
    current.upload_memory_limit = 4
    assert current.form["title"] == "bear"
    upload = current.files["file"]
    assert upload.size == len(payload) and upload.getvalue() == payload
    assert upload.stream._rolled
    current.close()
    assert upload.stream.closed
    current = Request(
        {"body": body, "headers": {"Content-Type": "multipart/form-data; boundary=boundary"}}
    )
    current.max_form_memory_size = 2
    with pytest.raises(RequestEntityTooLarge):
        _ = current.form
    current.close()
    malformed = Request(
        {"body": body[:-18], "headers": {"Content-Type": "multipart/form-data; boundary=boundary"}}
    )
    with pytest.raises(BadRequest):
        _ = malformed.files
    malformed.close()


def test_asgi_partial_input_and_limit_close_spools(monkeypatch):
    from higuma import gateways

    opened = []

    class TrackedBuffer(BodyBuffer):
        def __init__(self, *args):
            super().__init__(*args)
            opened.append(self)

    monkeypatch.setattr(gateways, "BodyBuffer", TrackedBuffer)
    app = Higuma(__name__, static_folder=None, max_content_length=8, request_memory_limit=2)

    @app.post("/")
    def index():
        return {"size": request.content_length}

    async def exercise(events):
        incoming = asyncio.Queue()
        for event in events:
            incoming.put_nowait(event)
        sent = []

        async def send(event):
            sent.append(event)

        await app.asgi_app({"type": "http", "method": "POST", "path": "/"}, incoming.get, send)
        return sent

    assert (
        asyncio.run(
            exercise(
                [
                    {"type": "http.request", "body": b"aaaa", "more_body": True},
                    {"type": "http.disconnect"},
                ]
            )
        )
        == []
    )
    assert asyncio.run(exercise([{"type": "http.request", "body": b"a" * 9}]))[0]["status"] == 413
    assert all(storage.closed for storage in opened)


def test_wsgi_read_failure_and_asgi_cancel_close_storage():
    from higuma.body import receive_body

    storage = BodyBuffer(16, 2)

    class IncompleteInput:
        def __init__(self):
            self.calls = 0

        def read(self, size):
            self.calls += 1
            if self.calls == 1:
                return b"aaaa"
            raise OSError("input failed")

    with pytest.raises(OSError, match="input failed"):
        storage.fill_from(IncompleteInput(), 8, False)
    assert storage.closed

    async def exercise():
        stored = BodyBuffer(16, 2)
        incoming = asyncio.Queue()
        incoming.put_nowait({"type": "http.request", "body": b"aaaa", "more_body": True})
        task = asyncio.create_task(receive_body(stored, incoming.get))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stored.closed

    asyncio.run(exercise())


def test_teardown_failure_still_closes_input_and_yield_dependency():
    from typing import Annotated

    from higuma import Depends

    app = Higuma(__name__, static_folder=None)
    closed, inputs = [], []

    def dependency():
        try:
            yield "resource"
        finally:
            closed.append(True)

    @app.get("/")
    def view(value: Annotated[str, Depends(dependency)]):
        inputs.append(request.stream)
        return value

    @app.teardown_request
    def fail(error):
        raise ValueError("teardown failed")

    with pytest.raises(ValueError, match="teardown failed"):
        app.test_client().get("/")
    assert closed == [True] and inputs[0].closed
