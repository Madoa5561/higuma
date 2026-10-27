import http.client
import json
import socket
import subprocess
import sys
import time
from tempfile import TemporaryFile

import pytest

APPLICATION = """
import sys
from examples.flask_migration import create_app

app = create_app()
port = int(sys.argv[1])
mode = sys.argv[2]
if mode == "native":
    app.run(port=port)
elif mode == "asgi":
    import uvicorn
    uvicorn.run(app.asgi_app, host="127.0.0.1", port=port, log_level="error", proxy_headers=False)
else:
    from examples.server_modes import LoopbackWSGIServer
    from wsgiref.simple_server import make_server
    with make_server("127.0.0.1", port, app.wsgi_app, server_class=LoopbackWSGIServer) as server:
        server.serve_forever()
"""


@pytest.mark.parametrize("mode", ["native", "asgi", "wsgi"])
def test_migration_example_real_http_in_all_server_modes(mode):
    if mode == "asgi":
        pytest.importorskip("uvicorn")
    psutil = pytest.importorskip("psutil")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    def request(method, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            connection.request(
                method, path, body=body, headers={"Content-Type": "application/json"}
            )
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), json.loads(response.read())
        finally:
            connection.close()

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
                    if request("GET", "/")[0] == 200:
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                pytest.fail("migration example did not start")
            status, headers, data = request("GET", "/ja/hello/%E7%86%8A")
            assert status == 200 and data == {
                "language": "ja",
                "name": "熊",
                "next": "/ja/hello/bear",
                "blueprints": ["pages"],
            }
            assert headers["x-project"] == "Higuma"
            assert request("GET", "/")[2]["hello"] == "/ja/hello/bear"
            assert request("POST", "/echo", "null")[2] is None
            assert request("POST", "/echo", "[1,2]")[2] == [1, 2]
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
