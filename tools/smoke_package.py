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

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("GET", "/values/" + "9" * 4500)
    response = connection.getresponse()
    assert response.status == 404, response.status
    response.read()
    connection.close()

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("GET", "/echo-path/")
    response = connection.getresponse()
    assert response.status == 200, response.status
    assert response.read() == b'{"path":"/echo-path/"}'
    connection.close()

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("HEAD", "/stream")
    response = connection.getresponse()
    assert response.status == 200, response.status
    assert response.read() == b""
    assert response.getheader("content-length") is None, response.getheaders()
    connection.close()


def _exercise_flask_helpers() -> None:
    from higuma import (
        Blueprint,
        Config,
        Higuma,
        Response,
        SessionMiddleware,
        StreamingResponse,
        after_this_request,
        has_request_context,
        request,
        session,
    )

    app = Higuma(__name__, static_folder=None)
    assert isinstance(app.config, Config)
    assert app.jsonify(None).data == b"null"
    assert app.jsonify(1, 2).get_json() == [1, 2]
    assert app.make_response().data == b""
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
        context_body = request.stream
    assert context_body.closed
    assert not has_request_context()
    client = app.test_client()
    response = client.get("/smoke/save")
    assert response.is_json and response.get_json() == {"ok": True}
    assert response.headers["X-Blueprint"] == "smoke"
    assert "cookie" in response.headers["Vary"].lower()
    assert client.get("/smoke/read").get_json() == {"saved": True}
    assert cleanup_paths == ["/smoke/read", "/smoke/save", "/smoke/read"]
    assigned = []

    @app.get("/assigned-background")
    def assigned_background():
        async def finish():
            await asyncio.sleep(0)
            assigned.append(request.path)

        res = Response("bear")
        res.background = finish
        return res

    @app.get("/sized-stream")
    def sized_stream():
        return StreamingResponse([b"be", b"ar"], content_length=4)

    @app.post("/created-tuple", status_code=201)
    def created_tuple():
        return {"created": True}, {"x-smoke": "1"}

    @app.get("/no-content")
    def no_content():
        return Response(b"", 204)

    assert client.get("/assigned-background").body == b"bear"
    assert assigned == ["/assigned-background"]
    assert client.head("/sized-stream").headers["content-length"] == "4"
    created = client.post("/created-tuple")
    assert created.status_code == 201 and created.headers["x-smoke"] == "1"
    assert "content-length" not in client.head("/no-content").headers
    from higuma import g

    with app.app_context():
        assert g.pop("missing", None) is None
        try:
            g.pop("missing")
        except KeyError:
            pass
        else:
            raise AssertionError("g.pop without default should raise KeyError")
    from dataclasses import dataclass

    @dataclass
    class SmokeBear:
        name: str

    from higuma import render_template

    assert '"name": "Higuma"' in render_template("x.html", bear=SmokeBear("Higuma")).context_json
    from decimal import Decimal
    from typing import Annotated

    from higuma import QueryParam

    @app.get("/decimal-smoke")
    def decimal_smoke(amount: Annotated[Decimal, QueryParam(ge=Decimal("0.01"))]):
        return {"amount": amount + Decimal("0.5")}

    assert client.get("/decimal-smoke?amount=2.25").json == {"amount": "2.75"}
    assert client.get("/decimal-smoke?amount=0.00").status_code == 422

    from higuma import Body

    @app.post("/bare-dict-smoke")
    def bare_dict_smoke(payload: Annotated[dict, Body()]):
        return {"ok": "k" in payload}

    assert client.post("/bare-dict-smoke", json={"k": 1}).json == {"ok": True}
    assert client.post("/bare-dict-smoke", json=[1, 2]).status_code == 422

    from higuma import EasyHiguma

    easy_app = EasyHiguma(__name__, db=":memory:", csrf=False, auth_routes=True, hasher_n=2**12)
    easy_memos = easy_app.table("memos", user_scoped=True)

    @easy_app.post("/easy-memos")
    @easy_app.login_required
    def easy_add_memo():
        return easy_memos.add(title=request.json["title"]), 201

    easy_client = easy_app.test_client()
    assert (
        easy_client.post("/register", json={"username": "smoke", "password": "secret"}).status_code
        == 201
    )
    assert easy_client.post("/easy-memos", json={"title": "hello"}).json["title"] == "hello"
    try:
        app.before_request("not-callable")  # type: ignore[arg-type]
    except TypeError:
        pass
    else:
        raise AssertionError("before_request must reject non-callables")
    from higuma import Headers

    assert 123 not in Headers({"Content-Type": "text/plain"})
    copied_headers = Response("ok", headers={"X-Smoke": "1"}).headers.copy()
    assert copied_headers["x-smoke"] == "1" and copied_headers["X-SMOKE"] == "1"
    try:
        Response(16)  # type: ignore[arg-type]
    except TypeError:
        pass
    else:
        raise AssertionError("Response(int) must raise TypeError")

    @app.get("/smoke-items/<slug>")
    def smoke_item(slug: str):
        return slug

    assert app.url_for("smoke_item", slug="bear") == "/smoke-items/bear"
    try:
        app.url_for("smoke_item", slug="")
    except ValueError:
        pass
    else:
        raise AssertionError("url_for must reject empty path parameters")
    multi = app.make_response(("ok", 200, [("Set-Cookie", "a=1"), ("Set-Cookie", "b=2")]))
    assert [v for k, v in multi.header_items if k == "set-cookie"] == ["a=1", "b=2"]
    from higuma import PasswordHasher

    hasher = PasswordHasher(n=2**15)
    encoded_pw = hasher.hash("smoke-pw")
    assert hasher.verify("smoke-pw", encoded_pw)
    assert not hasher.verify("smoke-pw", encoded_pw + "=")
    with app.test_request_context(
        "/smoke/read",
        headers={"cookie": "filter[page]=1; path=/admin; higuma_session=token"},
    ):
        assert request.cookies["higuma_session"] == "token"
        assert request.cookies["filter[page]"] == "1"
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

        from higuma import redirect

        @app.get("/go")
        def go():
            return redirect("/portal/health", 302)

        async with app.test_async_client(root_path="/portal") as async_client:
            followed = await async_client.get("/go", follow_redirects=True)
            assert followed.status_code == 200
            assert followed.get_json() == {"status": "ok"}

    asyncio.run(exercise())


def _exercise_developer_apis() -> None:
    from datetime import timedelta

    from higuma import (
        Blueprint,
        Database,
        HealthChecks,
        Higuma,
        Observability,
        Response,
        TrustedHostMiddleware,
        request,
        request_finished,
    )

    cookie_response = Response("ok")
    cookie_response.set_cookie("sid", "1", max_age=timedelta(seconds=90))
    assert any("Max-Age=90" in value for _, value in cookie_response.header_items)

    proxy_app = Higuma(__name__, static_folder=None)

    @proxy_app.get("/tag")
    def tag():
        if "value" in request.args:
            request.custom_tag = request.args["value"]
        return {"tag": getattr(request, "custom_tag", None)}

    proxy_client = proxy_app.test_client()
    assert proxy_client.get("/tag?value=bear").get_json() == {"tag": "bear"}
    assert proxy_client.get("/tag").get_json() == {"tag": None}
    proxy_app.config["MAX_FORM_PARTS"] = 7
    with proxy_app.test_request_context("/tag", base_url="http://example.test"):
        from higuma import Signal, StreamingResponse, url_for

        assert request.max_form_parts == 7
        assert url_for("tag", _external=True, _scheme="HTTPS") == "https://example.test/tag"

        pair_resp = Response("ok", headers=[("Set-Cookie", "a=1"), ("Set-Cookie", "b=2")])
        assert [v for k, v in pair_resp.header_items if k == "set-cookie"] == ["a=1", "b=2"]
        pair_stream = StreamingResponse(
            [b"ok"], headers=[("Set-Cookie", "a=1"), ("Set-Cookie", "b=2")], content_length=2
        )
        assert [v for k, v in pair_stream.header_items if k == "set-cookie"] == ["a=1", "b=2"]
        pair_stream._close()
        sig = Signal("smoke-value")
        hits = []

        def on_topic(sender, **kw):
            hits.append(sender)

        sig.connect(on_topic, sender="topic-x")
        sig.send(b"topic-x".decode("ascii"))
        assert hits == ["topic-x"]

    ipv6_app = Higuma(__name__, static_folder=None)
    ipv6_app.add_middleware(TrustedHostMiddleware, ["[::1]"])

    @ipv6_app.get("/ipv6")
    def ipv6():
        return {"ok": True}

    ipv6_client = ipv6_app.test_client()
    assert ipv6_client.get("/ipv6", headers={"host": "[0:0::1]:8000"}).status_code == 200
    assert ipv6_client.get("/ipv6", headers={"host": "[::1]:0"}).status_code == 403

    from higuma import Integer, Model, String

    class SmokeItem(Model):
        id = Integer(primary_key=True, autoincrement=True)
        name = String(nullable=False)

    smoke_db = Database("sqlite:///:memory:")
    smoke_db.create_all(SmokeItem)
    with smoke_db.session() as smoke_sess:
        smoke_sess.add(SmokeItem(name="bear"))
        smoke_sess.commit()
        try:
            smoke_sess.query(SmokeItem).filter_by(name="bear").limit(1).delete()
        except ValueError:
            pass
        else:
            raise AssertionError("limit().delete() must raise ValueError")
        assert smoke_sess.query(SmokeItem).count() == 1
    smoke_db.close()

    from decimal import Decimal

    from higuma import ServerSentEvent

    assert b'"12.50"' in ServerSentEvent(data={"amount": Decimal("12.50")}).encode()

    @ipv6_app.delete("/ipv6/<item_id>", status_code=204)
    def delete_ipv6(item_id: str) -> None:
        return None

    assert (
        "content"
        not in ipv6_app.openapi()["paths"]["/ipv6/{item_id}"]["delete"]["responses"]["204"]
    )

    import inspect

    from higuma import login_required

    @login_required
    async def smoke_protected():
        return {"ok": True}

    assert inspect.iscoroutinefunction(smoke_protected)
    try:
        login_required("not-callable")  # type: ignore[arg-type]
    except TypeError:
        pass
    else:
        raise AssertionError("login_required must reject non-callable target")

    try:
        TrustedHostMiddleware("example.com")  # type: ignore[arg-type]
    except TypeError:
        pass
    else:
        raise AssertionError("TrustedHostMiddleware must reject bare str")

    memory_db = Database("sqlite:///:memory:")
    with memory_db.session():
        pass
    memory_db.close()
    try:
        Database("sqlite:///   ")
    except ValueError:
        pass
    else:
        raise AssertionError("Database must reject whitespace-only SQLite path")

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

    result = app.test_cli_runner().invoke("greet bear")
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
import asyncio
import os
from higuma import Higuma, Response, StreamingResponse, request

app = Higuma(__name__, static_folder=None)

@app.get('/health')
def health():
    return {'status': 'ok'}

@app.get('/stream')
def stream():
    return StreamingResponse(iter([b'chunk']), media_type='text/plain')

@app.get('/values/<int:value>')
def value(value: int):
    return {'value': value, 'typed': isinstance(value, int)}

@app.get('/echo-path')
def echo_path():
    return {'path': request.path}

async def completed():
    await asyncio.sleep(0)
    app.state['background_path'] = request.path

@app.get('/background')
def background():
    app.state['background_body'] = request.stream
    return Response('bear', background=completed)

@app.get('/background-state')
def background_state():
    body = app.state.get('background_body')
    return {
        'done': app.state.get('background_path') == '/background',
        'closed': body is not None and body.closed,
    }

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
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", "/background")
        response = connection.getresponse()
        assert response.status == 200 and response.read() == b"bear"
        connection.close()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            connection.request("GET", "/background-state")
            response = connection.getresponse()
            state = response.read()
            connection.close()
            if state == b'{"done":true,"closed":true}':
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f"native background or body cleanup failed: {state!r}")
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
