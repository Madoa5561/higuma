from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from uuid import UUID

from higuma import (
    Blueprint,
    CORSMiddleware,
    Higuma,
    SecurityHeadersMiddleware,
    SessionMiddleware,
    abort,
    request,
)


class AppTests(unittest.TestCase):
    def make_app(self, **kwargs):
        return Higuma(__name__, static_folder=None, **kwargs)

    def test_basic_response_and_head(self):
        app = self.make_app()

        @app.get("/")
        def index():
            return "hello"

        client = app.test_client()
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "hello")
        self.assertEqual(response.media_type, "text/html; charset=utf-8")

        head = client.head("/")
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.body, b"")
        self.assertEqual(head.headers["content-length"], "5")

    def test_dynamic_routes_and_url_for(self):
        app = self.make_app()
        identifier = UUID("12345678-1234-5678-1234-567812345678")

        @app.get("/users/<int:user_id>/files/<path:filename>")
        def user_file(user_id: int, filename: str):
            return app.jsonify(
                user_id=user_id,
                filename=filename,
                typed=isinstance(user_id, int),
            )

        @app.get("/objects/<uuid:object_id>")
        def object_view(object_id: UUID):
            return str(object_id)

        client = app.test_client()
        response = client.get("/users/42/files/a/b.txt")
        self.assertEqual(
            response.json,
            {"user_id": 42, "filename": "a/b.txt", "typed": True},
        )
        self.assertEqual(client.get(f"/objects/{identifier}").text, str(identifier))
        self.assertEqual(
            app.url_for("user_file", user_id=7, filename="docs/read me.txt"),
            "/users/7/files/docs/read%20me.txt",
        )

    def test_request_json_query_form_and_proxy(self):
        app = self.make_app()

        @app.post("/inspect")
        def inspect_request():
            return app.jsonify(
                method=request.method,
                values=request.args.getlist("tag"),
                count=request.args.get("count", type=int),
                body=request.json,
            )

        @app.post("/form")
        def inspect_form(current):
            return app.jsonify(current.form.to_dict())

        client = app.test_client()
        response = client.post(
            "/inspect?tag=a&tag=b&count=3",
            json={"ok": True},
        )
        self.assertEqual(
            response.json,
            {
                "method": "POST",
                "values": ["a", "b"],
                "count": 3,
                "body": {"ok": True},
            },
        )
        self.assertEqual(
            client.post("/form", data={"name": "higuma"}).json,
            {"name": "higuma"},
        )

    def test_response_tuple_redirect_and_json(self):
        app = self.make_app()

        @app.get("/created")
        def created():
            return {"ok": True}, 201, {"x-result": "created"}

        response = app.test_client().get("/created")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers["x-result"], "created")
        self.assertEqual(response.json, {"ok": True})

    def test_error_handlers_and_abort(self):
        app = self.make_app()

        @app.get("/private")
        def private():
            abort(403, "no access")

        @app.errorhandler(403)
        def forbidden(error):
            return app.jsonify(error=error.detail, status=403), 403

        client = app.test_client()
        response = client.get("/private")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json["error"], "no access")
        self.assertEqual(client.get("/missing").status_code, 404)
        self.assertEqual(client.post("/private").status_code, 405)
        self.assertIn("GET", client.post("/private").headers["allow"])

    def test_hooks_and_middleware(self):
        app = self.make_app()
        events = []

        @app.before_request
        def before(current):
            events.append(("before", current.path))

        @app.after_request
        def after(response):
            events.append(("after", response.status_code))
            response.headers["x-after"] = "yes"
            return response

        @app.middleware
        def timing(current, call_next):
            events.append(("middleware-in", current.path))
            response = call_next(current)
            events.append(("middleware-out", current.path))
            return response

        @app.get("/")
        def index():
            return "ok"

        response = app.test_client().get("/")
        self.assertEqual(response.headers["x-after"], "yes")
        self.assertEqual(
            events,
            [
                ("middleware-in", "/"),
                ("before", "/"),
                ("middleware-out", "/"),
                ("after", 200),
            ],
        )

    def test_blueprint(self):
        app = self.make_app()
        api = Blueprint("api", __name__, url_prefix="/api")

        @api.get("/health")
        def health():
            return {"status": "ok"}

        app.register_blueprint(api)
        self.assertEqual(app.test_client().get("/api/health").json, {"status": "ok"})
        self.assertEqual(app.url_for("api.health"), "/api/health")

    def test_async_handler(self):
        app = self.make_app()

        @app.get("/async")
        async def async_view():
            await asyncio.sleep(0)
            return "async-ok"

        self.assertEqual(app.test_client().get("/async").text, "async-ok")

    def test_template_rendering(self):
        with tempfile.TemporaryDirectory() as directory:
            template_dir = Path(directory)
            (template_dir / "hello.html").write_text(
                "<h1>Hello {{ name }}</h1>",
                encoding="utf-8",
            )
            app = self.make_app(template_folder=str(template_dir))

            @app.context_processor
            def defaults():
                return {"name": "higuma"}

            @app.get("/")
            def index():
                return app.render_template("hello.html")

            response = app.test_client().get("/")
            self.assertEqual(response.status_code, 200)
            self.assertIn("Hello higuma", response.text)

    def test_static_files_etag_and_range(self):
        with tempfile.TemporaryDirectory() as directory:
            static_dir = Path(directory)
            (static_dir / "asset.txt").write_text("0123456789", encoding="utf-8")
            app = Higuma(
                __name__,
                static_folder=str(static_dir),
                static_url_path="/assets",
            )
            client = app.test_client()
            response = client.get("/assets/asset.txt")
            self.assertEqual(response.text, "0123456789")
            self.assertEqual(response.media_type, "text/plain")
            self.assertIn("etag", response.headers)

            not_modified = client.get(
                "/assets/asset.txt",
                headers={"if-none-match": response.headers["etag"]},
            )
            self.assertEqual(not_modified.status_code, 304)

            partial = client.get(
                "/assets/asset.txt",
                headers={"range": "bytes=2-5"},
            )
            self.assertEqual(partial.status_code, 206)
            self.assertEqual(partial.body, b"2345")

    def test_sessions_cors_and_security_headers(self):
        app = self.make_app()
        app.add_middleware(SessionMiddleware, "test-secret")
        app.add_middleware(
            CORSMiddleware,
            allow_origins=("https://example.com",),
            allow_credentials=True,
        )
        app.add_middleware(SecurityHeadersMiddleware)

        @app.get("/counter")
        def counter(current):
            current.session["count"] = current.session.get("count", 0) + 1
            return {"count": current.session["count"]}

        client = app.test_client()
        headers = {"origin": "https://example.com"}
        first = client.get("/counter", headers=headers)
        second = client.get("/counter", headers=headers)
        self.assertEqual(first.json, {"count": 1})
        self.assertEqual(second.json, {"count": 2})
        self.assertEqual(
            second.headers["access-control-allow-origin"],
            "https://example.com",
        )
        self.assertEqual(second.headers["x-content-type-options"], "nosniff")


if __name__ == "__main__":
    unittest.main()
