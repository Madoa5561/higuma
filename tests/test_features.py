from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from higuma import (
    AuthManager,
    Boolean,
    CSRFProtection,
    Database,
    Higuma,
    Integer,
    Model,
    OAuth2Client,
    PasswordHasher,
    RateLimitMiddleware,
    SessionMiddleware,
    String,
    TokenSigner,
    csrf_token,
    current_user,
    generate_user_id,
    request,
    validate_user_id,
)


class FeatureTests(unittest.TestCase):
    def make_app(self, **options):
        return Higuma(__name__, static_folder=None, **options)

    def test_multipart_fields_and_files(self):
        app = self.make_app()
        saved_paths = []

        @app.post("/upload")
        def upload():
            item = request.files["avatar"]
            with tempfile.TemporaryDirectory() as directory:
                destination = Path(directory) / "uploads"
                saved = item.save(destination)
                saved_paths.append((saved.name, saved.read_bytes()))
            return {
                "title": request.form["title"],
                "filename": item.filename,
                "content_type": item.content_type,
                "size": item.size,
                "content": item.read().decode(),
            }

        response = app.test_client().post(
            "/upload",
            data={"title": "bear"},
            files={"avatar": ("../..\\bear.txt", b"higuma", "text/plain")},
        )
        self.assertEqual(
            response.json,
            {
                "title": "bear",
                "filename": "bear.txt",
                "content_type": "text/plain",
                "size": 6,
                "content": "higuma",
            },
        )
        self.assertEqual(saved_paths, [("bear.txt", b"higuma")])

    def test_wsgi_and_asgi_mounts(self):
        app = self.make_app()

        def wsgi(environ, start_response):
            start_response("201 Created", [("Content-Type", "text/plain")])
            return [f"wsgi:{environ['PATH_INFO']}".encode()]

        async def asgi(scope, receive, send):
            event = await receive()
            await send(
                {
                    "type": "http.response.start",
                    "status": 202,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b"asgi:" + scope["path"].encode() + b":" + event["body"],
                }
            )

        app.mount_wsgi("/legacy", wsgi)
        app.mount_asgi("/modern", asgi)
        client = app.test_client()
        self.assertEqual(client.get("/legacy/users").text, "wsgi:/users")
        response = client.post("/modern/items", data="body")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.text, "asgi:/items:body")

    def test_openapi_generation_and_docs(self):
        app = self.make_app()

        @app.get("/users/<int:user_id>", tags=("users",), summary="Get user")
        def get_user(user_id: int) -> dict[str, int]:
            return {"user_id": user_id}

        document = app.test_client().get("/openapi.json").json
        operation = document["paths"]["/users/{user_id}"]["get"]
        self.assertEqual(operation["summary"], "Get user")
        self.assertEqual(operation["parameters"][0]["schema"], {"type": "integer"})
        self.assertIn("swagger-ui", app.test_client().get("/docs").text)

    def test_template_cache_can_be_cleared(self):
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "index.html"
            template.write_text("first", encoding="utf-8")
            app = self.make_app(template_folder=directory)

            @app.get("/")
            def index():
                return app.render_template("index.html")

            client = app.test_client()
            self.assertEqual(client.get("/").text, "first")
            template.write_text("second", encoding="utf-8")
            app.clear_template_cache()
            self.assertEqual(client.get("/").text, "second")

    def test_database_crud_and_transactions(self):
        class User(Model):
            id = Integer(primary_key=True, autoincrement=True)
            email = String(unique=True, nullable=False, index=True)
            active = Boolean(default=True)

        with tempfile.TemporaryDirectory() as directory:
            database = Database(f"sqlite:///{Path(directory) / 'app.db'}")
            database.create_all(User)
            with database.session() as session:
                user = session.add(User(email="bear@example.com"))
                self.assertIsNotNone(user.id)
            with database.session() as session:
                loaded = session.query(User).filter_by(email="bear@example.com").first()
                self.assertIsNotNone(loaded)
                self.assertTrue(loaded.active)
                loaded.active = False
                session.save(loaded)
            with database.session() as session:
                self.assertEqual(session.query(User).count(), 1)
                self.assertFalse(session.query(User).get(user.id).active)

        memory_database = Database("sqlite:///:memory:")
        memory_database.create_all(User)
        with memory_database.session() as session:
            session.add(User(email="memory@example.com"))
        with memory_database.session() as session:
            self.assertEqual(session.query(User).count(), 1)

    def test_password_tokens_and_user_ids(self):
        hasher = PasswordHasher(n=2**10)
        encoded = hasher.hash("correct horse battery staple")
        self.assertTrue(hasher.verify("correct horse battery staple", encoded))
        self.assertFalse(hasher.verify("wrong", encoded))
        self.assertFalse(hasher.needs_rehash(encoded))

        signer = TokenSigner("test-secret")
        token = signer.dumps({"user_id": "usr_12345678"})
        self.assertEqual(
            signer.loads(token, max_age=60),
            {"user_id": "usr_12345678"},
        )
        user_id = generate_user_id()
        self.assertTrue(validate_user_id(user_id))
        self.assertFalse(validate_user_id("../../etc/passwd"))

    def test_auth_csrf_and_rate_limit(self):
        @dataclass
        class User:
            id: str
            is_authenticated: bool = True
            roles: tuple[str, ...] = ("admin",)
            permissions: tuple[str, ...] = ("posts:write",)

        users = {"usr_12345678": User("usr_12345678")}
        app = self.make_app()
        auth = AuthManager(app, secret_key="test-secret", user_loader=users.get)
        app.add_middleware(CSRFProtection)

        @app.get("/csrf")
        def get_csrf():
            return {"token": csrf_token()}

        @app.post("/login")
        def login():
            auth.login_user(users["usr_12345678"])
            return {"ok": True}

        @app.get("/me")
        @auth.login_required
        def me():
            return {"id": current_user.id}

        @app.get("/admin")
        @auth.roles_required("admin")
        @auth.permissions_required("posts:write")
        @auth.fresh_login_required
        def admin():
            return {"allowed": True}

        client = app.test_client()
        token = client.get("/csrf").json["token"]
        self.assertEqual(
            client.post("/login", headers={"x-csrf-token": token}).status_code,
            200,
        )
        self.assertEqual(client.get("/me").json, {"id": "usr_12345678"})
        self.assertEqual(client.get("/admin").json, {"allowed": True})
        self.assertEqual(client.post("/login").status_code, 403)

        limited = self.make_app()
        limited.add_middleware(RateLimitMiddleware, limit=1, window=60)

        @limited.get("/")
        def index():
            return "ok"

        limited_client = limited.test_client()
        self.assertEqual(limited_client.get("/").status_code, 200)
        self.assertEqual(limited_client.get("/").status_code, 429)

    def test_oauth_provider_presets_and_websocket_registration(self):
        oauth = OAuth2Client.google(
            client_id="client",
            client_secret="secret",
            redirect_uri="https://example.com/callback",
            secret_key="state-secret",
        )
        url = oauth.authorization_url(prompt="consent")
        self.assertIn("accounts.google.com", url)
        self.assertIn("state=", url)

        app = self.make_app()
        app.add_middleware(SessionMiddleware, "oauth-session-secret")

        @app.get("/oauth/start")
        def oauth_start():
            return {"url": oauth.authorization_url()}

        @app.get("/oauth/validate")
        def oauth_validate():
            try:
                oauth.validate_state(request.args["state"])
            except ValueError:
                return {"valid": False}, 400
            return {"valid": True}

        client = app.test_client()
        authorization_url = client.get("/oauth/start").json["url"]
        query = parse_qs(urlsplit(authorization_url).query)
        self.assertEqual(query["code_challenge_method"], ["S256"])
        state = query["state"][0]
        self.assertEqual(client.get(f"/oauth/validate?state={state}").status_code, 200)
        self.assertEqual(client.get(f"/oauth/validate?state={state}").status_code, 400)

        @app.websocket("/ws/<string:room>")
        def socket(ws, room):
            ws.send_json({"room": room})

        self.assertIn("socket", app._websocket_rules)

    def test_websocket_uses_session_auth_middleware(self):
        @dataclass
        class User:
            id: str
            is_authenticated: bool = True

        user = User("usr_socket123")
        app = self.make_app()
        auth = AuthManager(
            app,
            secret_key="socket-secret",
            user_loader=lambda user_id: user if user_id == user.id else None,
        )

        @app.post("/login")
        def login():
            auth.login_user(user)
            return {"ok": True}

        @app.websocket("/private")
        @auth.login_required
        def private_socket(ws):
            ws.send_json({"user_id": current_user.id})

        client = app.test_client()
        client.post("/login")
        cookie = "; ".join(f"{key}={value}" for key, value in client.cookies.items())
        sent = []

        class Session:
            def send_text(self, value):
                sent.append(value)

            def send_bytes(self, value):
                sent.append(value)

            def close(self, code, reason):
                pass

        route = app._websocket_rules["private_socket"]
        route.callback(
            {
                "method": "GET",
                "path": "/private",
                "headers": {"cookie": cookie},
                "path_params": {},
            },
            Session(),
        )
        self.assertIn("usr_socket123", sent[0])


if __name__ == "__main__":
    unittest.main()
