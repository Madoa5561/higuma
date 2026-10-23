import base64
import http.client
import json
import socket
import subprocess
import sys
import time

import pytest

APPLICATION = r"""
import sys
from higuma import FileResponse, Higuma

app = Higuma(__name__, static_folder=None)

@app.get("/api/hello")
def hello():
    return {"message": "hello"}

@app.post("/submit")
def submit():
    return {"ok": True}

@app.get("/download")
def download():
    return FileResponse(sys.argv[1] + "/index.html", headers={"etag": '"part,revision"'})

@app.websocket("/ws")
def websocket(ws):
    ws.close()

app.mount_spa("/", sys.argv[1])
app.run(port=int(sys.argv[2]))
"""


class Server:
    def __init__(self, port):
        self.port = port

    def request(self, method, path, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()


@pytest.fixture(scope="module")
def csr_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("csr-server")
    (root / "index.html").write_text("<html>CSR shell</html>", encoding="utf-8")
    (root / "assets").mkdir()
    (root / "assets/app.js").write_text("console.log('ready')", encoding="utf-8")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = Server(port)
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", APPLICATION, str(root), str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=3)
                raise RuntimeError(f"CSR server exited: {stdout}\n{stderr}")
            try:
                if server.request("GET", "/api/hello")[0] == 200:
                    break
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(0.05)
        else:
            raise TimeoutError("CSR server did not start")
        yield server
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        process.communicate(timeout=5)


def test_real_csr_deep_links_mime_cache_and_api_contract(csr_server):
    server = csr_server
    status, headers, body = server.request(
        "GET", "/screens/%E6%97%A5%E6%9C%AC", {"Accept": "text/html"}
    )
    assert status == 200 and body == b"<html>CSR shell</html>"
    assert headers["content-type"].startswith("text/html")
    assert headers["cache-control"] == "no-cache"
    status, _, body = server.request("GET", "/api/hello")
    assert status == 200 and json.loads(body) == {"message": "hello"}
    for path in ("/api/missing", "/assets/missing.js", "/assets/missing"):
        assert server.request("GET", path, {"Accept": "text/html"})[0] == 404
    assert server.request("GET", "/screen", {"Accept": "application/json"})[0] == 404
    assert server.request("GET", "/submit")[0] == 405
    assert server.request("OPTIONS", "/submit")[1]["allow"] == "OPTIONS, POST"


def test_real_csr_asset_head_and_if_range(csr_server):
    server = csr_server
    status, headers, body = server.request("GET", "/assets/app.js")
    assert status == 200 and headers["content-type"].startswith("text/javascript")
    assert headers["x-content-type-options"] == "nosniff"
    status, head_headers, head_body = server.request("HEAD", "/assets/app.js")
    assert status == 200 and head_body == b""
    assert int(head_headers["content-length"]) == len(body)
    assert (
        server.request("GET", "/assets/app.js", {"If-None-Match": f"W/{headers['etag']}"})[0] == 304
    )
    assert (
        server.request("GET", "/assets/app.js", {"If-Modified-Since": headers["last-modified"]})[0]
        == 304
    )
    status, _, part = server.request(
        "GET", "/assets/app.js", {"Range": "bytes=0-6", "If-Range": headers["etag"]}
    )
    assert status == 206 and part == b"console"
    status, _, full = server.request(
        "GET", "/assets/app.js", {"Range": "bytes=0-6", "If-Range": '"old"'}
    )
    assert status == 200 and full == body


def test_root_spa_does_not_replace_websocket_upgrade(csr_server):
    with socket.create_connection(("127.0.0.1", csr_server.port), timeout=3) as connection:
        key = base64.b64encode(b"csr-test-key-16!").decode("ascii")
        request = (
            f"GET /ws HTTP/1.1\r\nHost: 127.0.0.1:{csr_server.port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        connection.sendall(request.encode("ascii"))
        response = connection.recv(4096)
        assert response.startswith(b"HTTP/1.1 101 "), response


def test_real_file_etag_with_commas(csr_server):
    server = csr_server
    assert server.request("GET", "/download", {"If-None-Match": '"part,revision"'})[0] == 304
    assert server.request("GET", "/download", {"If-Match": '"old", "part,revision"'})[0] == 200
    assert server.request("GET", "/download", {"If-Match": 'W/"part,revision"'})[0] == 412
