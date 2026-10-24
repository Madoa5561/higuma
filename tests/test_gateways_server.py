import asyncio
import http.client
import json
import socket
import subprocess
import sys
import time

import pytest


@pytest.fixture(scope="module", params=["native", "asgi", "wsgi"])
def gateway_server(request):
    mode = request.param
    if mode == "asgi":
        pytest.importorskip("uvicorn")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    if mode == "asgi":
        command = [
            sys.executable,
            "-m",
            "uvicorn",
            "examples.server_modes:asgi_app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--lifespan",
            "on",
            "--ws",
            "websockets-sansio",
            "--no-proxy-headers",
            "--log-level",
            "warning",
        ]
    else:
        command = [
            sys.executable,
            "-m",
            "examples.server_modes",
            "--mode",
            mode,
            "--port",
            str(port),
        ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=2)
                raise RuntimeError(f"Gateway exited: {stdout}\n{stderr}")
            try:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                connection.request("GET", "/")
                response = connection.getresponse()
                data = response.read()
                connection.close()
                if response.status == 200 and json.loads(data)["ready"]:
                    break
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(0.05)
        else:
            raise TimeoutError("Gateway server did not start")
        yield mode, port
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        process.communicate(timeout=5)


def test_gateway_real_http_json_head_and_sse(gateway_server):
    _mode, port = gateway_server
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request(
            "POST",
            "/echo",
            body='{"bear":"日本"}'.encode("utf-16"),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read()) == {"received": {"bear": "日本"}}
        connection.request("HEAD", "/")
        response = connection.getresponse()
        assert response.status == 200 and response.read() == b""
        connection.request("GET", "/events")
        response = connection.getresponse()
        assert response.status == 200
        assert response.getheader("content-type").startswith("text/event-stream")
        payload = response.read()
        assert payload.count(b"event: count") == 3
        assert b'"number":2' in payload
    finally:
        connection.close()


def test_gateway_real_websocket_echo_and_origin(gateway_server):
    mode, port = gateway_server
    if mode == "wsgi":
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            connection.request(
                "GET", "/ws", headers={"Upgrade": "websocket", "Connection": "Upgrade"}
            )
            response = connection.getresponse()
            assert response.status == 404
            response.read()
        finally:
            connection.close()
        return
    websockets = pytest.importorskip("websockets.asyncio.client")
    errors = pytest.importorskip("websockets.exceptions")

    async def exercise():
        uri = f"ws://127.0.0.1:{port}/ws"
        async with websockets.connect(uri, origin=f"http://127.0.0.1:{port}") as client:
            await client.send('{"bear":"日本"}')
            assert json.loads(await asyncio.wait_for(client.recv(), 3)) == {
                "echo": {"bear": "日本"}
            }
        with pytest.raises(errors.InvalidStatus):
            async with websockets.connect(uri, origin="https://evil.example"):
                pass

    asyncio.run(exercise())
