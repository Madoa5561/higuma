import http.client
import json
import socket
import subprocess
import sys
import time
from tempfile import TemporaryFile

import pytest


def test_async_ecosystem_real_uvicorn_http_and_database():
    pytest.importorskip("aiosqlite")
    pytest.importorskip("uvicorn")
    psutil = pytest.importorskip("psutil")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    def request(method, path, data=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            connection.request(
                method,
                path,
                body=json.dumps(data).encode() if data else None,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), json.loads(response.read())
        finally:
            connection.close()

    with TemporaryFile(mode="w+t", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "examples.async_ecosystem:create_asgi_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--lifespan",
                "on",
                "--no-proxy-headers",
                "--log-level",
                "error",
            ],
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
                    if request("GET", "/health/ready")[0] == 200:
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                pytest.fail("ASGI example did not become ready")
            status, headers, data = request("POST", "/notes", {"title": "日本の熊"})
            assert status == 201 and data == {"title": "日本の熊"}
            assert "x-request-id" in headers
            assert request("GET", "/notes")[2] == {"titles": ["日本の熊"]}
            assert request("GET", "/health/live")[2] == {"status": "alive"}
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
