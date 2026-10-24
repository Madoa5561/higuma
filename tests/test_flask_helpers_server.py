import http.client
import json
import os
import socket
import subprocess
import sys
import time
from http.cookies import SimpleCookie

import pytest

APPLICATION = r"""
import sys
from examples.flask_lifecycle import create_app
from higuma import request

app = create_app()

@app.teardown_request
def record_cleanup(error):
    app.state["last_cleanup"] = {
        "path": request.path,
        "error": type(error).__name__ if error is not None else None,
    }

@app.get("/cleanup")
def cleanup():
    return app.state.get("last_cleanup", {})

app.run(port=int(sys.argv[1]))
"""


class LifecycleServer:
    def __init__(self, port):
        self.port = port
        self.cookies = {}

    def request(self, method, path, body=None, headers=None):
        request_headers = dict(headers or {})
        if self.cookies:
            request_headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            for name, value in response.getheaders():
                if name.lower() == "set-cookie":
                    cookie = SimpleCookie()
                    cookie.load(value)
                    self.cookies.update({key: morsel.value for key, morsel in cookie.items()})
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()


@pytest.fixture(scope="module")
def lifecycle_server():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = os.environ.copy()
    environment["HIGUMA_SECRET_KEY"] = "lifecycle-server-test-secret-at-least-32-bytes"
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", APPLICATION, str(port)],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    server = LifecycleServer(port)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=3)
                raise RuntimeError(f"Lifecycle server exited: {stdout}\n{stderr}")
            try:
                if server.request("GET", "/cleanup")[0] == 200:
                    break
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(0.05)
        else:
            raise TimeoutError("Lifecycle server did not start")
        yield server
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        process.communicate(timeout=5)


def test_real_blueprint_csrf_session_callbacks_and_cleanup(lifecycle_server):
    server = lifecycle_server
    status, headers, body = server.request("GET", "/counter/")
    assert status == 200 and "server-timing" in headers
    initial = json.loads(body)
    assert initial["count"] == 0
    payload = '{"increment":2}'.encode("utf-16")
    content_headers = {"Content-Type": "application/json"}
    assert server.request("POST", "/counter/increment", payload, content_headers)[0] == 403
    content_headers["X-CSRF-Token"] = initial["csrf_token"]
    status, headers, body = server.request("POST", "/counter/increment", payload, content_headers)
    assert status == 200 and json.loads(body) == {"count": 2}
    assert headers["x-counter-saved"] == "yes"
    assert "cookie" in headers["vary"].lower()
    assert json.loads(server.request("GET", "/cleanup")[2]) == {
        "path": "/counter/increment",
        "error": None,
    }
    assert json.loads(server.request("GET", "/counter/")[2])["count"] == 2
    status, _, body = server.request("POST", "/counter/increment", b'"\xff"', content_headers)
    assert status == 400 and "increment" in json.loads(body)["error"]
    assert server.request("GET", "/counter/missing")[0] == 404
