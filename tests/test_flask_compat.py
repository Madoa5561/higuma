import asyncio
import importlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from urllib.parse import quote

import pytest
from higuma import (
    Blueprint,
    Higuma,
    Response,
    SessionMiddleware,
    current_app,
    flash,
    g,
    get_flashed_messages,
    has_app_context,
    has_request_context,
    request,
    session,
    url_for,
)


def test_application_context_nesting_and_teardown():
    app = Higuma(__name__, static_folder=None)
    other = Higuma(__name__, static_folder=None)
    seen = []

    @app.teardown_appcontext
    def teardown(error):
        seen.append((g.get("value"), error))

    assert not has_app_context()
    with app.app_context():
        assert has_app_context() and not has_request_context()
        g.value = "outer"
        app.extensions["example"] = object()
        with other.app_context():
            assert "value" not in g
            g.value = "inner"
            assert current_app._get_current() is other
        assert g.pop("value") == "outer"
        g.setdefault("value", "restored")
    assert seen == [("restored", None)]
    assert not has_app_context()
    with pytest.raises(RuntimeError, match="application context"):
        g.value = "unbound"
    error = ValueError("example")
    with pytest.raises(ValueError), app.app_context():
        raise error
    assert seen[-1] == (None, error)
    assert not has_app_context()


def test_request_globals_session_and_flash_are_isolated():
    app = Higuma(__name__, static_folder=None)
    app.add_middleware(SessionMiddleware, "a-test-secret-with-at-least-32-bytes")

    @app.post("/flash")
    async def create_flash():
        assert has_request_context()
        g.value = "this request"
        await asyncio.sleep(0)
        assert g.value == "this request"
        session["visits"] = session.get("visits", 0) + 1
        session.permanent = True
        flash("saved", "success")
        return {"endpoint": request.endpoint}

    @app.get("/messages")
    def messages():
        assert "value" not in g
        return {
            "messages": get_flashed_messages(True),
            "again": get_flashed_messages(category_filter=("success",)),
            "visits": session.get("visits", 0),
            "permanent": session.permanent,
        }

    client = app.test_client()
    assert client.post("/flash").json == {"endpoint": "create_flash"}
    assert client.get("/messages").json == {
        "messages": [["success", "saved"]],
        "again": ["saved"],
        "visits": 1,
        "permanent": True,
    }
    assert client.get("/messages").json["messages"] == []
    assert app.test_client().get("/messages").json["visits"] == 0
    assert not has_request_context() and not has_app_context()
    with pytest.raises(RuntimeError, match="request context"):
        session["bad"] = 1


def test_concurrent_requests_keep_independent_globals():
    app = Higuma(__name__, static_folder=None)
    barrier = Barrier(2, timeout=5)

    @app.get("/<name>")
    def index(name):
        g.name = name
        barrier.wait()
        return {"name": g.name}

    def read(name):
        result = app.test_client().get(f"/{name}").json
        assert not has_app_context() and not has_request_context()
        return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(read, ["first", "second"])) == [
            {"name": "first"},
            {"name": "second"},
        ]


@pytest.mark.parametrize("converter,value", [("int", "１２"), ("float", "١.٢")])
def test_python_numeric_routes_match_rust_ascii_policy(converter, value):
    app = Higuma(__name__, static_folder=None)

    @app.get(f"/<{converter}:number>")
    def index(number):
        return {"number": number}

    assert app.test_client().get("/" + quote(value)).status_code == 404


def test_url_for_and_direct_error_registration():
    app = Higuma(__name__, static_folder=None)
    bp = Blueprint("pages", __name__, url_prefix="/pages")

    @bp.get("/<int:number>")
    def page(number):
        return {
            "url": url_for(".page", number=number, tag=["a", "日本"], _anchor="section 1"),
            "external": url_for(".page", number=number, _external=True, _scheme="https"),
            "blueprint": request.blueprint,
        }

    app.register_blueprint(bp)
    result = app.test_client().get("/pages/2").json
    assert result["url"] == "/pages/2?tag=a&tag=%E6%97%A5%E6%9C%AC#section%201"
    assert result["external"] == "https://localhost/pages/2"
    assert result["blueprint"] == "pages"
    app.config["SERVER_NAME"] = "example.test"
    assert app.url_for("pages.page", number=1, _external=True) == "http://example.test/pages/1"
    with pytest.raises(ValueError):
        app.url_for("pages.page", number=1, _scheme="https")
    app.register_error_handler(404, lambda error: ("custom", 404))
    assert app.test_client().get("/missing").text == "custom"


def test_response_headers_are_case_insensitive_and_union_is_validated():
    response = Response(headers={"X-Test": "one"})
    assert response.headers["X-TEST"] == "one"
    assert "X-Test" in response.headers
    response.headers |= {"X-Test": "two"}
    assert response.headers.get("X-Test") == "two"
    with pytest.raises(ValueError):
        response.headers |= {"X-Test": "bad\r\nInjected: yes"}
    assert response.headers.pop("X-Test") == "two"


def test_template_helper_preserves_reserved_template_variables():
    from higuma import render_template

    app = Higuma(__name__, static_folder=None)
    app.context_processor(lambda: {"status": "default", "extra": "injected"})
    with app.app_context():
        response = render_template("example.html", status="active", headers={"label": "value"})
    assert response.status_code == 200
    assert response.context == {
        "status": "active",
        "headers": {"label": "value"},
        "extra": "injected",
    }


def test_real_flask_wsgi_unicode_urls_cookies_and_body():
    flask = pytest.importorskip("flask")
    legacy = flask.Flask(__name__)
    legacy.secret_key = "flask-integration-test-secret-not-production"

    @legacy.route("/hello/<name>", methods=["GET", "POST"])
    def hello(name):
        flask.session["count"] = flask.session.get("count", 0) + 1
        return {
            "name": name,
            "count": flask.session["count"],
            "url": flask.url_for("hello", name=name),
            "header": flask.request.headers.get("X-User"),
            "body": flask.request.get_json(silent=True),
        }

    @legacy.get("/redirect")
    def redirected():
        return flask.redirect(flask.url_for("hello", name="bear"))

    app = Higuma(__name__, static_folder=None)
    app.mount_wsgi("/legacy", legacy)
    client = app.test_client()
    target = "/legacy/hello/" + quote("日本語")
    response = client.post(
        target, json={"hello": "world"}, headers={"X-User": "trusted", "X_User": "ignored"}
    )
    assert response.status_code == 200
    assert response.json == {
        "name": "日本語",
        "count": 1,
        "url": target,
        "header": "trusted",
        "body": {"hello": "world"},
    }
    assert client.get(target).json["count"] == 2
    assert client.get("/legacy/redirect").headers["Location"] == "/legacy/hello/bear"


def test_asgi_path_is_decoded_but_raw_path_is_preserved():
    app = Higuma(__name__, static_folder=None)

    async def service(scope, receive, send):
        assert scope["path"] == "/日本"
        assert scope["raw_path"] == b"/%E6%97%A5%E6%9C%AC"
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    app.mount_asgi("/asgi", service)
    assert app.test_client().get("/asgi/%E6%97%A5%E6%9C%AC").text == "ok"


def test_factory_cli_syntax(monkeypatch):
    from higuma.cli import load_app

    module = importlib.import_module("examples.blueprint_hooks")
    monkeypatch.setattr(module, "create_app", lambda: Higuma(__name__, static_folder=None))
    assert isinstance(load_app("examples.blueprint_hooks:create_app()"), Higuma)


def test_security_example_requires_password_and_csrf(monkeypatch):
    from higuma import PasswordHasher

    monkeypatch.setenv("HIGUMA_SECRET_KEY", "example-test-secret-at-least-32-bytes")
    monkeypatch.setenv("HIGUMA_DEMO_PASSWORD_HASH", PasswordHasher().hash("correct-password"))
    module = importlib.import_module("examples.security_tokens_roles")
    client = module.create_app().test_client()
    assert client.post("/session", json={"user_id": "usr_example123"}).status_code == 403
    headers = {"X-CSRF-Token": client.get("/csrf-token").json["csrf_token"]}
    assert (
        client.post("/session", json={"user_id": "usr_example123"}, headers=headers).status_code
        == 401
    )
    assert (
        client.post(
            "/session", json={"user_id": "usr_example123", "password": "wrong"}, headers=headers
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/session",
            json={"user_id": "usr_example123", "password": "correct-password"},
            headers=headers,
        ).status_code
        == 200
    )
    assert client.get("/admin").status_code == 200
