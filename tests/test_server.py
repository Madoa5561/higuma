from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_APPLICATION = r"""
import asyncio
import os

from pathlib import Path
from typing import Annotated

from higuma import (
    BackgroundTasks,
    Depends,
    EventSourceResponse,
    FileResponse,
    Higuma,
    Response,
    ServerSentEvent,
    StreamingResponse,
    request,
)


app = Higuma(
    __name__,
    static_folder=os.environ["HIGUMA_STATIC_DIR"],
    static_url_path="/static",
    max_content_length=16,
)


@app.middleware
def reject_one_websocket(current, call_next):
    if current.path == "/ws-denied":
        return Response("sign in", 302, {"location": "/login"})
    return call_next(current)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/typed/<int:number>/<float:ratio>")
def typed(number, ratio):
    return {
        "number": number,
        "number_type": type(number).__name__,
        "ratio": ratio,
        "ratio_type": type(ratio).__name__,
    }


@app.post("/body")
def body():
    return {"length": len(request.body)}


@app.get("/status/<int:code>")
def status(code):
    return Response(
        "body-must-not-leak",
        code,
        {
            "content-length": "999",
            "transfer-encoding": "chunked",
        },
    )


@app.get("/headers")
def headers():
    response = Response(
        "headers",
        headers={
            "connection": "x-private",
            "x-private": "must-not-leak",
            "keep-alive": "timeout=5",
            "proxy-connection": "keep-alive",
            "te": "trailers",
            "trailer": "x-checksum",
            "transfer-encoding": "chunked",
            "upgrade": "websocket",
        },
    )
    response.set_cookie("first", "one")
    response.set_cookie("second", "two")
    return response


@app.get("/stream")
def stream(background_tasks: BackgroundTasks):
    def chunks():
        yield b"one"
        yield "two"

    background_tasks.add_task(
        Path(os.environ["HIGUMA_BACKGROUND_FILE"]).write_text,
        "complete",
        encoding="utf-8",
    )
    return StreamingResponse(chunks(), media_type="text/plain; charset=utf-8")


def stream_resource():
    yield "resource"
    Path(os.environ["HIGUMA_CLEANUP_FILE"]).write_text(
        request.path,
        encoding="utf-8",
    )


@app.get("/dependency-stream")
def dependency_stream(resource: Annotated[str, Depends(stream_resource)]):
    def chunks():
        yield resource

    return StreamingResponse(chunks(), media_type="text/plain; charset=utf-8")


@app.get("/async-stream")
def async_stream():
    async def chunks():
        yield "async-"
        await asyncio.sleep(0)
        yield b"stream"

    return StreamingResponse(chunks(), media_type="text/plain; charset=utf-8")


@app.get("/events")
def events():
    return EventSourceResponse(
        [ServerSentEvent({"status": "ready"}, event="status", id="1")]
    )


@app.get("/download")
def download():
    return FileResponse(Path(os.environ["HIGUMA_STATIC_DIR"]) / "asset.bin")


@app.websocket("/ws")
def websocket_echo(ws):
    ws.send_text("echo:" + ws.receive_text())
    ws.close(1000, "complete")


@app.websocket("/ws-denied")
def denied_websocket(ws):
    ws.send_text("must-not-upgrade")


app.run(host="127.0.0.1", port=int(os.environ["HIGUMA_TEST_PORT"]), workers=1)
"""


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _read_exact(connection: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = connection.recv(length - len(chunks))
        if not chunk:
            raise EOFError(f"socket closed after {len(chunks)} of {length} bytes")
        chunks.extend(chunk)
    return bytes(chunks)


def _read_http_headers(connection: socket.socket) -> tuple[int, list[tuple[str, str]], bytes]:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = connection.recv(4096)
        if not chunk:
            raise EOFError("socket closed before the HTTP headers completed")
        data.extend(chunk)
    header_block, remainder = bytes(data).split(b"\r\n\r\n", 1)
    lines = header_block.decode("iso-8859-1").split("\r\n")
    status = int(lines[0].split(" ", 2)[1])
    headers = []
    for line in lines[1:]:
        name, value = line.split(":", 1)
        headers.append((name.lower(), value.strip()))
    return status, headers, remainder


def _masked_frame(opcode: int, payload: bytes) -> bytes:
    if len(payload) >= 126:
        raise ValueError("test frames must use the short WebSocket payload form")
    mask = b"\x12\x34\x56\x78"
    masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return bytes((0x80 | opcode, 0x80 | len(payload))) + mask + masked


def _read_frame(connection: socket.socket, initial: bytes = b"") -> tuple[int, bytes]:
    buffer = bytearray(initial)

    def take(length: int) -> bytes:
        while len(buffer) < length:
            buffer.extend(connection.recv(4096))
        result = bytes(buffer[:length])
        del buffer[:length]
        return result

    first, second = take(2)
    if first & 0x70:
        raise AssertionError("server frame used reserved WebSocket bits")
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", take(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", take(8))[0]
    if second & 0x80:
        raise AssertionError("server frames must not be masked")
    return first & 0x0F, take(length)


class RealServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.static_directory = Path(cls.temporary_directory.name) / "static"
        cls.static_directory.mkdir()
        cls.asset = (b"0123456789" * 1024) + b"end"
        (cls.static_directory / "asset.bin").write_bytes(cls.asset)
        cls.background_file = Path(cls.temporary_directory.name) / "background.txt"
        cls.cleanup_file = Path(cls.temporary_directory.name) / "cleanup.txt"
        cls.port = _free_port()
        environment = os.environ.copy()
        environment["HIGUMA_TEST_PORT"] = str(cls.port)
        environment["HIGUMA_STATIC_DIR"] = str(cls.static_directory)
        environment["HIGUMA_BACKGROUND_FILE"] = str(cls.background_file)
        environment["HIGUMA_CLEANUP_FILE"] = str(cls.cleanup_file)
        cls.process = subprocess.Popen(
            [sys.executable, "-u", "-c", _APPLICATION],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if cls.process.poll() is not None:
                stdout, stderr = cls.process.communicate(timeout=2)
                raise RuntimeError(f"server exited early\nstdout:\n{stdout}\nstderr:\n{stderr}")
            try:
                status, _, body = cls.request("GET", "/health")
                if status == 200 and body == b'{"status":"ok"}':
                    return
            except OSError:
                pass
            time.sleep(0.05)
        cls.tearDownClass()
        raise TimeoutError("real higuma test server did not become ready")

    @classmethod
    def tearDownClass(cls) -> None:
        process = getattr(cls, "process", None)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)
        temporary_directory = getattr(cls, "temporary_directory", None)
        if temporary_directory is not None:
            temporary_directory.cleanup()

    @classmethod
    def request(
        cls,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, list[tuple[str, str]], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.getheaders(), response.read()
        finally:
            connection.close()

    @classmethod
    @contextmanager
    def websocket_handshake(
        cls, path: str
    ) -> Iterator[tuple[socket.socket, int, dict[str, str], bytes]]:
        connection = socket.create_connection(("127.0.0.1", cls.port), timeout=5)
        connection.settimeout(5)
        key = base64.b64encode(b"higuma-test-key!").decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{cls.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        connection.sendall(request.encode("ascii"))
        status, header_items, remainder = _read_http_headers(connection)
        response_headers = dict(header_items)
        try:
            if status == 101:
                expected = base64.b64encode(
                    hashlib.sha1(
                        (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
                    ).digest()
                ).decode("ascii")
                if response_headers.get("sec-websocket-accept") != expected:
                    raise AssertionError("invalid Sec-WebSocket-Accept response")
            yield connection, status, response_headers, remainder
        finally:
            connection.close()

    def test_http_methods_head_and_typed_routes(self) -> None:
        status, headers, body = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, b'{"status":"ok"}')
        self.assertEqual(dict(headers)["content-length"], "15")

        status, headers, body = self.request("HEAD", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(dict(headers)["content-length"], "15")

        status, headers, body = self.request("OPTIONS", "/health")
        self.assertEqual(status, 204)
        self.assertEqual(body, b"")
        self.assertNotIn("content-length", dict(headers))
        self.assertIn("GET", dict(headers)["allow"])

        status, headers, _ = self.request("POST", "/health")
        self.assertEqual(status, 405)
        self.assertIn("GET", dict(headers)["allow"])

        status, _, body = self.request("GET", "/typed/-12/3.25")
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(body),
            {
                "number": -12,
                "number_type": "int",
                "ratio": 3.25,
                "ratio_type": "float",
            },
        )

    def test_bodyless_status_framing(self) -> None:
        for status_code in (204, 205, 304):
            with self.subTest(status=status_code):
                status, headers, body = self.request("GET", f"/status/{status_code}")
                self.assertEqual(status, status_code)
                self.assertEqual(body, b"")
                content_length = dict(headers).get("content-length")
                if status_code == 205:
                    self.assertIn(content_length, (None, "0"))
                else:
                    self.assertIsNone(content_length)
                self.assertNotIn("transfer-encoding", dict(headers))

        status, headers, remainder = self.request("GET", "/status/103")
        self.assertEqual(status, 500)
        self.assertNotIn(b"body-must-not-leak", remainder)
        self.assertNotIn("transfer-encoding", dict(headers))

    def test_duplicate_cookies_and_hop_by_hop_headers(self) -> None:
        status, headers, body = self.request("GET", "/headers")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"headers")
        cookies = [value for name, value in headers if name.lower() == "set-cookie"]
        self.assertEqual(len(cookies), 2)
        self.assertTrue(any(value.startswith("first=one") for value in cookies))
        self.assertTrue(any(value.startswith("second=two") for value in cookies))
        names = {name.lower() for name, _ in headers}
        self.assertTrue(
            names.isdisjoint(
                {
                    "connection",
                    "keep-alive",
                    "proxy-connection",
                    "te",
                    "trailer",
                    "transfer-encoding",
                    "upgrade",
                    "x-private",
                }
            )
        )

    def test_request_body_limit(self) -> None:
        status, _, body = self.request("POST", "/body", body=b"x" * 16)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"length": 16})

        status, _, body = self.request("POST", "/body", body=b"x" * 17)
        self.assertEqual(status, 413)
        self.assertIn(b"too large", body)

    def test_streaming_sse_background_and_file_ranges(self) -> None:
        status, headers, body = self.request("GET", "/stream")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"onetwo")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not self.background_file.exists():
            time.sleep(0.01)
        self.assertEqual(self.background_file.read_text(encoding="utf-8"), "complete")

        status, _, body = self.request("GET", "/dependency-stream")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"resource")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not self.cleanup_file.exists():
            time.sleep(0.01)
        self.assertEqual(
            self.cleanup_file.read_text(encoding="utf-8"),
            "/dependency-stream",
        )

        status, _, body = self.request("GET", "/async-stream")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"async-stream")

        status, headers, body = self.request(
            "GET",
            "/events",
            headers={"accept-encoding": "gzip"},
        )
        self.assertEqual(status, 200)
        self.assertNotIn("content-encoding", dict(headers))
        self.assertEqual(
            body,
            b'event: status\nid: 1\ndata: {"status":"ready"}\n\n',
        )

        status, download_headers, body = self.request("GET", "/download")
        self.assertEqual(status, 200)
        self.assertEqual(body, self.asset)
        self.assertEqual(dict(download_headers)["accept-ranges"], "bytes")
        etag = dict(download_headers)["etag"]

        status, headers, body = self.request(
            "GET",
            "/download",
            headers={"range": "bytes=2-5"},
        )
        self.assertEqual(status, 206)
        self.assertEqual(body, self.asset[2:6])
        self.assertEqual(dict(headers)["content-range"], f"bytes 2-5/{len(self.asset)}")

        status, headers, body = self.request(
            "HEAD",
            "/download",
            headers={"range": "bytes=-4"},
        )
        self.assertEqual(status, 206)
        self.assertEqual(body, b"")
        self.assertEqual(dict(headers)["content-length"], "4")

        status, _, body = self.request(
            "GET",
            "/download",
            headers={"if-none-match": etag},
        )
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")

        status, headers, body = self.request(
            "GET",
            "/download",
            headers={"range": "bytes=999999-"},
        )
        self.assertEqual(status, 416)
        self.assertEqual(body, b"")
        self.assertEqual(dict(headers)["content-range"], f"bytes */{len(self.asset)}")

        status, _, _ = self.request(
            "GET",
            "/download",
            headers={"range": "bytes=0-1,4-5"},
        )
        self.assertEqual(status, 416)

        range_cases = (
            ("bytes=2-5", self.asset[2:6], f"bytes 2-5/{len(self.asset)}"),
            (
                "bytes=-4",
                self.asset[-4:],
                f"bytes {len(self.asset) - 4}-{len(self.asset) - 1}/{len(self.asset)}",
            ),
            (
                "bytes=10240-",
                self.asset[10240:],
                f"bytes 10240-{len(self.asset) - 1}/{len(self.asset)}",
            ),
        )
        for range_header, expected, content_range in range_cases:
            with self.subTest(range=range_header):
                status, headers, body = self.request(
                    "GET",
                    "/static/asset.bin",
                    headers={"range": range_header, "accept-encoding": "gzip"},
                )
                self.assertEqual(status, 206)
                self.assertEqual(body, expected)
                self.assertEqual(dict(headers)["content-range"], content_range)
                self.assertEqual(dict(headers)["content-length"], str(len(expected)))
                self.assertNotIn("content-encoding", dict(headers))

        status, headers, body = self.request(
            "HEAD",
            "/static/asset.bin",
            headers={"range": "bytes=2-5"},
        )
        self.assertEqual(status, 206)
        self.assertEqual(body, b"")
        self.assertEqual(dict(headers)["content-length"], "4")

        status, headers, body = self.request(
            "GET",
            "/static/asset.bin",
            headers={"range": f"bytes={len(self.asset) + 1}-"},
        )
        self.assertEqual(status, 416)
        self.assertEqual(body, b"")
        self.assertEqual(dict(headers)["content-range"], f"bytes */{len(self.asset)}")

    def test_websocket_echo_and_server_close(self) -> None:
        with self.websocket_handshake("/ws") as (connection, status, headers, remainder):
            self.assertEqual(status, 101)
            self.assertEqual(headers.get("upgrade", "").lower(), "websocket")
            self.assertEqual(remainder, b"")
            connection.sendall(_masked_frame(0x1, b"hello"))
            opcode, payload = _read_frame(connection)
            self.assertEqual((opcode, payload), (0x1, b"echo:hello"))
            opcode, payload = _read_frame(connection)
            self.assertEqual(opcode, 0x8)
            self.assertEqual(struct.unpack("!H", payload[:2])[0], 1000)
            self.assertEqual(payload[2:], b"complete")
            connection.sendall(_masked_frame(0x8, struct.pack("!H", 1000)))

    def test_websocket_preflight_must_return_no_content(self) -> None:
        with self.websocket_handshake("/ws-denied") as (_, status, headers, remainder):
            self.assertEqual(status, 302)
            self.assertEqual(headers.get("location"), "/login")
            self.assertNotIn("sec-websocket-accept", headers)
            self.assertIn(remainder, (b"", b"sign in"))


if __name__ == "__main__":
    unittest.main()
