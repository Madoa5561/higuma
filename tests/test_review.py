from __future__ import annotations

import unittest
from dataclasses import dataclass
from unittest.mock import patch

from higuma import (
    AuthManager,
    Boolean,
    CORSMiddleware,
    Database,
    Higuma,
    Integer,
    Model,
    OAuth2Client,
    ProxyHeadersMiddleware,
    Response,
    SessionMiddleware,
    String,
    Supervisor,
    TokenSigner,
    current_user,
    request,
    secure_filename,
)
from higuma.mounts import _asgi_scope, _wsgi_environ
from higuma.openapi import schema_for, swagger_ui_html
from higuma.request import Request

SECRET = "review-secret-key-that-is-at-least-32-bytes"


class ReviewRegressionTests(unittest.TestCase):
    def make_app(self, **options):
        return Higuma(__name__, static_folder=None, **options)

    def test_response_rejects_invalid_status_and_headers(self):
        with self.assertRaises(ValueError):
            Response("bad", 99)
        with self.assertRaises(ValueError):
            Response("bad", headers={"x-test": "safe\r\ninjected: yes"})
        with self.assertRaises(ValueError):
            Response("bad", headers={"bad header": "value"})
        response = Response("ok")
        with self.assertRaises(ValueError):
            response.headers["x-test"] = "safe\r\ninjected: yes"

    def test_swagger_escapes_configuration_and_openapi_ids_are_unique(self):
        html = swagger_ui_html(
            "</script><script>alert(1)</script>",
            "</title><script>alert(2)</script>",
        )
        self.assertNotIn("</title><script>", html)
        self.assertNotIn("</script><script>alert(1)", html)

        app = self.make_app()

        @app.route("/items", methods=("GET", "POST"))
        def items():
            return {}

        operations = app.openapi()["paths"]["/items"]
        self.assertNotEqual(
            operations["get"]["operationId"],
            operations["post"]["operationId"],
        )

        @dataclass
        class Node:
            value: str
            child: Node | None = None

        schemas = {}
        self.assertEqual(schema_for(Node, schemas), {"$ref": "#/components/schemas/Node"})
        self.assertIn("Node", schemas)

    def test_cors_automatic_preflight_runs_through_middleware(self):
        app = self.make_app()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=("https://example.com",),
            allow_methods=("POST", "OPTIONS"),
            allow_headers=("content-type",),
        )

        @app.post("/items")
        def create_item():
            return {}, 201

        response = app.test_client().options(
            "/items",
            headers={
                "origin": "https://example.com",
                "access-control-request-method": "POST",
                "access-control-request-headers": "Content-Type",
            },
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            response.headers["access-control-allow-origin"],
            "https://example.com",
        )

        denied = app.test_client().options(
            "/items",
            headers={
                "origin": "https://example.com",
                "access-control-request-method": "DELETE",
            },
        )
        self.assertEqual(denied.status_code, 403)

    def test_remember_login_controls_cookie_persistence(self):
        @dataclass
        class User:
            id: str
            is_authenticated: bool = True

        user = User("usr_remember")
        app = self.make_app()
        auth = AuthManager(
            app,
            secret_key=SECRET,
            user_loader=lambda user_id: user if user_id == user.id else None,
        )

        @app.post("/login")
        def login():
            auth.login_user(user, remember=request.args.get("remember") == "1")
            return {"ok": True}

        transient = app.test_client().post("/login?remember=0")
        transient_cookie = next(
            value for name, value in transient.header_items if name == "set-cookie"
        )
        self.assertNotIn("Max-Age", transient_cookie)

        persistent = app.test_client().post("/login?remember=1")
        persistent_cookie = next(
            value for name, value in persistent.header_items if name == "set-cookie"
        )
        self.assertIn("Max-Age", persistent_cookie)

    def test_weak_signing_keys_are_rejected(self):
        with self.assertRaises(ValueError):
            TokenSigner("short")
        with self.assertRaises(ValueError):
            SessionMiddleware("short")
        middleware = SessionMiddleware(SECRET)
        oversized = middleware._load(None)
        oversized["value"] = "x" * 5000
        with self.assertRaisesRegex(ValueError, "4093"):
            middleware._dump(oversized)
        self.assertEqual(dict(middleware._load("é.signature")), {})

    def test_invalid_configuration_and_charsets_are_rejected(self):
        with self.assertRaises(ValueError):
            self.make_app(max_content_length=0)
        with self.assertRaises(ValueError):
            self.make_app().run(processes=0)
        with self.assertRaises(ValueError):
            CORSMiddleware(allow_origins=("*",), allow_credentials=True)

        app = self.make_app()

        @app.post("/text")
        def text():
            return request.text

        response = app.test_client().post(
            "/text",
            data=b"value",
            headers={"content-type": "text/plain; charset=not-a-real-charset"},
        )
        self.assertEqual(response.status_code, 400)

    def test_proxy_headers_require_a_trusted_peer(self):
        app = self.make_app()

        @app.get("/")
        def inspect():
            return {"client": request.client_addr, "scheme": request.scheme}

        untrusted = app.test_client().get(
            "/",
            headers={
                "x-forwarded-for": "203.0.113.10",
                "x-forwarded-proto": "https",
            },
        )
        self.assertEqual(untrusted.json, {"client": "127.0.0.1", "scheme": "http"})

        trusted_app = self.make_app()
        trusted_app.add_middleware(
            ProxyHeadersMiddleware,
            trusted_proxies=("127.0.0.0/8",),
        )

        @trusted_app.get("/")
        def trusted_inspect():
            return {"client": request.client_addr, "scheme": request.scheme}

        trusted = trusted_app.test_client().get(
            "/",
            headers={
                "x-forwarded-for": "198.51.100.99, 203.0.113.10, 127.0.0.2",
                "x-forwarded-proto": "http, https",
            },
        )
        self.assertEqual(trusted.json, {"client": "203.0.113.10", "scheme": "https"})

    def test_falsey_authenticated_users_are_supported(self):
        @dataclass
        class User:
            id: str
            is_authenticated: bool = True

            def __bool__(self):
                return False

        user = User("usr_falsey")
        app = self.make_app()
        auth = AuthManager(app, secret_key=SECRET, user_loader=lambda _user_id: user)

        @app.post("/login")
        def login():
            auth.login_user(user)
            return {"ok": True}

        @app.get("/private")
        @auth.login_required
        def private():
            return {"id": current_user.id}

        client = app.test_client()
        client.post("/login")
        self.assertEqual(client.get("/private").json, {"id": "usr_falsey"})

    def test_mounts_preserve_duplicate_request_headers(self):
        mounted_request = Request(
            {
                "path": "/mounted",
                "raw_headers": [
                    (b"host", b"localhost"),
                    (b"cookie", b"a=1"),
                    (b"cookie", b"b=2"),
                    (b"x-value", b"one"),
                    (b"x-value", b"two"),
                ],
                "body": b"",
                "client_addr": "127.0.0.1",
            }
        )
        scope = _asgi_scope(mounted_request, "/")
        self.assertEqual(
            [value for key, value in scope["headers"] if key == b"x-value"],
            [b"one", b"two"],
        )
        environ = _wsgi_environ(mounted_request, "/")
        self.assertEqual(environ["HTTP_COOKIE"], "a=1; b=2")
        self.assertEqual(environ["HTTP_X_VALUE"], "one,two")

    def test_database_null_pagination_and_delete_guard(self):
        class Item(Model):
            id = Integer(primary_key=True, autoincrement=True)
            name = String(nullable=True)
            active = Boolean(default=True)

        database = Database("sqlite:///:memory:")
        database.create_all(Item)
        with database.session() as session:
            session.add(Item(name=None))
            session.add(Item(name="second"))
            session.add(Item(name="third"))
        with database.session() as session:
            self.assertEqual(session.query(Item).filter_by(name=None).count(), 1)
            page = session.query(Item).order_by("id").offset(1).limit(1).all()
            self.assertEqual([item.name for item in page], ["second"])
            with self.assertRaises(ValueError):
                session.query(Item).delete()
            self.assertEqual(session.query(Item).delete_all(), 3)

    def test_upload_names_and_invalid_ranges_are_safe(self):
        self.assertEqual(secure_filename("../../CON.txt"), "_CON.txt")
        self.assertEqual(secure_filename("..\x00/"), "upload")

        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            static = Path(directory)
            (static / "asset.txt").write_text("abc", encoding="utf-8")
            app = Higuma(__name__, static_folder=directory)
            response = app.test_client().get(
                "/static/asset.txt",
                headers={"range": "bytes=99-100"},
            )
            self.assertEqual(response.status_code, 416)
            self.assertEqual(response.headers["content-range"], "bytes */3")

    def test_routing_is_deterministic_and_registration_is_atomic(self):
        app = self.make_app()

        @app.get("/values/<string:value>")
        def string_value(value):
            return {"type": "string"}

        @app.get("/values/<int:value>")
        def int_value(value):
            return {"type": "int"}

        self.assertEqual(app.test_client().get("/values/12").json["type"], "int")
        self.assertEqual(app.test_client().get("/values/a%2Fb").status_code, 404)
        self.assertEqual(app.test_client().get("/values/%FF").status_code, 404)
        route_count = len(app._routes)
        with self.assertRaises(ValueError):

            @app.get("/values/<int:value>", endpoint="duplicate")
            def duplicate(value):
                return value

        self.assertEqual(len(app._routes), route_count)
        self.assertNotIn("duplicate", app._endpoint_rules)

    def test_websocket_auth_is_checked_before_handler(self):
        app = self.make_app()
        auth = AuthManager(app, secret_key=SECRET, user_loader=lambda _user_id: None)

        @app.websocket("/private")
        @auth.login_required
        def private(ws):
            ws.send_text("private")

        route = app._websocket_rules["private"]
        raw = {
            "method": "GET",
            "path": "/private",
            "headers": {"host": "localhost"},
            "path_params": {},
            "client_addr": "127.0.0.1",
        }
        response = app._dispatch_websocket_preflight(route, raw)
        self.assertEqual(response.status_code, 401)

    def test_after_hook_errors_do_not_leak_details(self):
        app = self.make_app()

        @app.after_request
        def broken_after(response):
            raise RuntimeError("database-password=super-secret")

        @app.get("/")
        def index():
            return "ok"

        response = app.test_client().get("/")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("super-secret", response.text)

    def test_oauth_provider_response_is_bounded(self):
        oauth = OAuth2Client.google(
            client_id="client",
            client_secret="provider-secret",
            redirect_uri="https://example.com/callback",
            secret_key=SECRET,
        )

        class LargeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, size):
                return b"x" * size

        with (
            patch("higuma.auth.urlopen", return_value=LargeResponse()),
            self.assertRaisesRegex(RuntimeError, "exceeded"),
        ):
            oauth.userinfo("token")

    def test_supervisor_restarts_failed_workers_with_a_limit(self):
        supervisor = Supervisor("example:app", processes=2, max_restarts=1)
        old_worker = object()
        replacement = object()
        supervisor._children = [old_worker]
        with (
            patch.object(supervisor, "_start_worker", return_value=replacement),
            patch("higuma.supervisor._wait_for_port"),
        ):
            supervisor._restart_worker(0, 8123, 1)
            self.assertIs(supervisor._children[0], replacement)
            with self.assertRaisesRegex(RuntimeError, "restart limit"):
                supervisor._restart_worker(0, 8123, 1)

    def test_rate_limiter_bounds_identity_storage(self):
        from higuma import RateLimitMiddleware

        middleware = RateLimitMiddleware(limit=2, max_keys=2)
        for address in ("192.0.2.1", "192.0.2.2", "192.0.2.3"):
            raw = {
                "client_addr": address,
                "headers": {},
            }
            middleware(Request(raw), lambda _request: Response("ok"))
        self.assertEqual(len(middleware._requests), 2)
        self.assertNotIn("192.0.2.1", middleware._requests)


if __name__ == "__main__":
    unittest.main()
