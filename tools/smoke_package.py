from __future__ import annotations

import argparse
import asyncio
import http.client
import io
import os
import socket
import subprocess
import sys
import tempfile
import time
import venv
from pathlib import Path


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _single_wheel(directory: Path) -> Path:
    wheels = list(directory.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one wheel in {directory}, found {len(wheels)}")
    return wheels[0].resolve()


def _smoke_wheel_in_isolation(directory: Path, expected_version: str | None) -> None:
    wheel = _single_wheel(directory)
    with tempfile.TemporaryDirectory(prefix="higuma-smoke-") as temporary_directory:
        environment_path = Path(temporary_directory) / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(environment_path)
        python = environment_path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        environment = os.environ.copy()
        environment["PYTHONNOUSERSITE"] = "1"
        environment.pop("PYTHONPATH", None)
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-deps",
                "--disable-pip-version-check",
                str(wheel),
            ],
            check=True,
            cwd=temporary_directory,
            env=environment,
        )
        command = [str(python), str(Path(__file__).resolve())]
        if expected_version:
            command.extend(("--expected-version", expected_version))
        subprocess.run(command, check=True, cwd=temporary_directory, env=environment)


def _wait_for_server(process: subprocess.Popen[str], port: int) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=2)
            raise RuntimeError(f"server exited early\nstdout:\n{stdout}\nstderr:\n{stderr}")
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
            connection.request("GET", "/health")
            response = connection.getresponse()
            body = response.read()
            connection.close()
            if response.status == 200 and body == b'{"status":"ok"}':
                return
        except OSError:
            pass
        time.sleep(0.1)
    raise TimeoutError("higuma smoke server did not become ready")


def _exercise_http(port: int) -> None:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("HEAD", "/health")
    response = connection.getresponse()
    assert response.status == 200, response.status
    assert response.read() == b""
    assert response.getheader("content-length") == "15"
    connection.close()

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("GET", "/values/42")
    response = connection.getresponse()
    assert response.status == 200, response.status
    assert response.read() == b'{"value":42,"typed":true}'
    connection.close()


def _exercise_flask_helpers() -> None:
    from higuma import (
        Blueprint,
        Higuma,
        SessionMiddleware,
        after_this_request,
        has_request_context,
        request,
        session,
    )

    app = Higuma(__name__, static_folder=None)
    app.add_middleware(SessionMiddleware, "package-smoke-test-secret-at-least-32-bytes")
    blueprint = Blueprint("smoke", __name__, url_prefix="/smoke")
    cleanup_paths = []
    blueprint.teardown_request(lambda error: cleanup_paths.append(request.path))

    @blueprint.after_request
    def scoped(response):
        response.headers["X-Blueprint"] = "smoke"
        return response

    @blueprint.get("/save")
    def save():
        @after_this_request
        def persist(response):
            session["saved"] = True
            return response

        return {"ok": True}

    @blueprint.get("/read")
    def read():
        return {"saved": session.get("saved", False)}

    app.register_blueprint(blueprint)
    with app.test_request_context("/smoke/read", base_url="https://example.test"):
        assert request.endpoint == "smoke.read"
        assert request.host_url == "https://example.test/"
    assert not has_request_context()
    client = app.test_client()
    response = client.get("/smoke/save")
    assert response.is_json and response.get_json() == {"ok": True}
    assert response.headers["X-Blueprint"] == "smoke"
    assert "cookie" in response.headers["Vary"].lower()
    assert client.get("/smoke/read").get_json() == {"saved": True}
    assert cleanup_paths == ["/smoke/read", "/smoke/save", "/smoke/read"]
    assert not has_request_context()


def _exercise_gateways() -> None:
    from wsgiref.util import setup_testing_defaults
    from wsgiref.validate import validator

    from higuma import Higuma

    app = Higuma(__name__, static_folder=None)

    @app.get("/health")
    async def health():
        await asyncio.sleep(0)
        return {"status": "ok"}

    environ = {}
    setup_testing_defaults(environ)
    environ.update(PATH_INFO="/health", QUERY_STRING="", CONTENT_LENGTH="0")
    environ["wsgi.input"] = io.BytesIO()
    received = []

    def start_response(status, headers, exc_info=None):
        received.append(status)

    result = validator(app.wsgi_app)(environ, start_response)
    try:
        assert b"".join(result) == b'{"status":"ok"}'
        assert received == ["200 OK"]
    finally:
        result.close()

    async def exercise():
        incoming = asyncio.Queue()
        incoming.put_nowait({"type": "http.request", "body": b""})
        events = []

        async def send(event):
            events.append(event)

        await asyncio.wait_for(
            app.asgi_app(
                {"type": "http", "path": "/health", "headers": [(b"host", b"localhost")]},
                incoming.get,
                send,
            ),
            5,
        )
        assert events[0]["status"] == 200
        assert b"".join(event.get("body", b"") for event in events) == b'{"status":"ok"}'

    asyncio.run(exercise())


def _exercise_developer_apis() -> None:
    from higuma import Blueprint, HealthChecks, Higuma, Observability, request, request_finished

    app = Higuma(__name__, static_folder=None, request_memory_limit=4)
    parent = Blueprint("parent", __name__, url_prefix="/parent")
    child = Blueprint("child", __name__, url_prefix="/child")
    child.get("/")(lambda: {"nested": True})
    parent.register_blueprint(child)
    app.register_blueprint(parent)
    assert app.test_client().get("/parent/child").get_json() == {"nested": True}

    @app.cli.command()
    def greet(name: str):
        return "hello " + name

    result = app.test_cli_runner().invoke(["greet", "bear"])
    assert result.exit_code == 0 and result.output.strip() == "hello bear"

    events = []

    def completed(sender, response):
        events.append(response.status_code)

    with request_finished.connected_to(completed, app):
        assert app.test_client().get("/parent/child").status_code == 200
    assert events == [200]
    HealthChecks().install(app)
    assert app.test_client().get("/health/live").status_code == 200
    assert Observability().snapshot()["requests"] == 0

    @app.get("/fast")
    async def fast():
        return {"fast": request.state.get("_higuma_async_fast_path", False)}

    async def exercise():
        async with app.test_async_client() as client:
            assert (await client.get("/parent/child")).get_json() == {"nested": True}
            assert (await client.get("/fast")).get_json() == {"fast": True}

    asyncio.run(exercise())


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test an installed higuma package")
    parser.add_argument("--wheel-dir", type=Path, help="install the single wheel from this folder")
    parser.add_argument("--expected-version")
    args = parser.parse_args()

    if args.wheel_dir:
        _smoke_wheel_in_isolation(args.wheel_dir, args.expected_version)
        return 0

    import higuma

    if args.expected_version and higuma.__version__ != args.expected_version:
        raise RuntimeError(
            f"installed version {higuma.__version__} does not match {args.expected_version}"
        )

    cli = subprocess.run(
        [sys.executable, "-m", "higuma", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    if f"higuma {higuma.__version__}" not in cli.stdout:
        raise RuntimeError(f"unexpected CLI version output: {cli.stdout!r}")

    _exercise_flask_helpers()
    _exercise_gateways()
    _exercise_developer_apis()

    port = _free_port()
    application = """
import os
from higuma import Higuma

app = Higuma(__name__, static_folder=None)

@app.get('/health')
def health():
    return {'status': 'ok'}

@app.get('/values/<int:value>')
def value(value: int):
    return {'value': value, 'typed': isinstance(value, int)}

app.run(host='127.0.0.1', port=int(os.environ['HIGUMA_SMOKE_PORT']), workers=1)
"""
    environment = os.environ.copy()
    environment["HIGUMA_SMOKE_PORT"] = str(port)
    process = subprocess.Popen(
        [sys.executable, "-c", application],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_server(process, port)
        _exercise_http(port)
    finally:
        process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)

    print(f"higuma {higuma.__version__} package smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
