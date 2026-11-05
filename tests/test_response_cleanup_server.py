import http.client
import json
import socket
import subprocess
import sys
import time
from tempfile import TemporaryFile

import pytest

APPLICATION = """
import asyncio
import io
import sys
from higuma import Higuma, Response, StreamingResponse, request

app = Higuma(__name__, static_folder=None)
events, bodies = [], []

async def after():
    await asyncio.sleep(0)
    events.append("after:" + request.path)

@app.get("/background")
def background():
    bodies.append(request.stream)
    return Response("bear", background=after)

class Chunks(io.BytesIO):
    def close(self):
        if not self.closed:
            events.append("custom-close")
        super().close()

@app.get("/finite")
def finite():
    return StreamingResponse(Chunks(b"bear"), background=after)

def stream():
    bodies.append(request.stream)
    async def chunks():
        try:
            while True:
                yield b"x" * 1024
                await asyncio.sleep(0.02)
        finally:
            events.append("generator-close")
    return StreamingResponse(chunks(), background=after)

async def async_stream():
    return stream()

app.get("/stream")(async_stream if sys.argv[2] == "asgi-fast" else stream)

@app.get("/state")
def state():
    return {"events": list(events), "bodies_closed": all(body.closed for body in bodies)}

port, mode = int(sys.argv[1]), sys.argv[2]
if mode == "native":
    app.run(port=port)
elif mode.startswith("asgi"):
    import uvicorn
    uvicorn.run(app.asgi_app, host="127.0.0.1", port=port, log_level="error", proxy_headers=False)
else:
    from examples.server_modes import LoopbackWSGIServer
    from wsgiref.simple_server import make_server
    with make_server("127.0.0.1", port, app.wsgi_app, server_class=LoopbackWSGIServer) as server:
        server.serve_forever()
"""


@pytest.mark.parametrize("mode", ["native", "asgi", "asgi-fast", "wsgi"])
def test_real_http_background_stream_close_and_disconnect_cleanup(mode):
    if mode.startswith("asgi"):
        pytest.importorskip("uvicorn")
    psutil = pytest.importorskip("psutil")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    def fetch(path, method="GET"):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request(method, path)
            response = connection.getresponse()
            assert response.status == 200
            return response.read()
        finally:
            connection.close()

    def wait_for(predicate):
        deadline = time.monotonic() + 8
        state = None
        while time.monotonic() < deadline:
            state = json.loads(fetch("/state"))
            if predicate(state):
                return state
            time.sleep(0.05)
        pytest.fail(f"cleanup did not complete: {state}")

    with TemporaryFile(mode="w+t", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-u", "-c", APPLICATION, str(port), mode],
            stdout=subprocess.DEVNULL,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    log.seek(0)
                    pytest.fail(log.read())
                try:
                    fetch("/state")
                    break
                except OSError:
                    time.sleep(0.05)
            else:
                pytest.fail("cleanup test server did not start")
            assert fetch("/background") == b"bear"
            wait_for(lambda state: "after:/background" in state["events"])
            assert fetch("/finite") == b"bear"
            wait_for(lambda state: "after:/finite" in state["events"])
            assert fetch("/finite", "HEAD") == b""
            wait_for(lambda state: state["events"].count("after:/finite") == 2)

            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            try:
                connection.request("GET", "/stream")
                response = connection.getresponse()
                assert response.read(1) == b"x"
                if connection.sock is not None:
                    connection.sock.shutdown(socket.SHUT_RDWR)
                response.close()
            finally:
                connection.close()
            state = wait_for(
                lambda state: "after:/stream" in state["events"] and state["bodies_closed"]
            )
            assert state["events"].count("generator-close") == 1
            assert state["events"].count("custom-close") == 2
            assert state["events"].count("after:/background") == 1
            assert state["events"].count("after:/stream") == 1
            assert state["bodies_closed"]
        finally:
            try:
                children = psutil.Process(process.pid).children(recursive=True)
            except psutil.NoSuchProcess:
                children = []
            for child in children:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            _, alive = psutil.wait_procs(children, timeout=3)
            for child in alive:
                child.kill()
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
