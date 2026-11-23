from __future__ import annotations

import asyncio
import unittest
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Annotated, Any, TypedDict
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from higuma import (
    CSRFProtection,
    Database,
    Date,
    Float,
    Higuma,
    HTTPException,
    Integer,
    Model,
    OAuth2Client,
    PasswordHasher,
    Query,
    Response,
    Session,
    SessionMiddleware,
    csrf_token,
    current_app,
    make_response,
    request,
    secure_filename,
)
from higuma.openapi import schema_for

SECRET = "python-regression-secret-that-is-at-least-32-bytes"


class PythonCoreRegressionTests(unittest.TestCase):
    def make_app(self) -> Higuma:
        return Higuma(
            __name__,
            static_folder=None,
            openapi_url=None,
            docs_url=None,
        )

    def test_secure_filename_sanitizes_the_fallback(self):
        self.assertEqual(secure_filename("", fallback="../../escape"), "escape")
        self.assertEqual(secure_filename("", fallback="..\\..\\CON.txt"), "_CON.txt")
        self.assertEqual(secure_filename("", fallback="../../"), "upload")

    def test_async_handler_keeps_request_and_application_contexts(self):
        app = self.make_app()

        @app.get("/async-context")
        async def async_context():
            await asyncio.sleep(0)
            return {"path": request.path, "app": current_app.import_name}

        async def invoke_from_running_loop():
            return app.test_client().get("/async-context")

        response = asyncio.run(invoke_from_running_loop())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json,
            {"path": "/async-context", "app": __name__},
        )

    def test_session_tracks_all_direct_mutators(self):
        session = Session()
        self.assertEqual(session.setdefault("created", 1), 1)
        self.assertTrue(session.modified)

        session.modified = False
        self.assertEqual(session.setdefault("created", 2), 1)
        self.assertFalse(session.modified)

        session |= {"updated": 2}
        self.assertTrue(session.modified)

        session.modified = False
        key, value = session.popitem()
        self.assertIn((key, value), {("created", 1), ("updated", 2)})
        self.assertTrue(session.modified)

        session.modified = False
        self.assertIsNone(session.pop("missing", None))
        self.assertFalse(session.modified)

    def test_csrf_helper_follows_custom_field_name(self):
        app = self.make_app()
        app.add_middleware(SessionMiddleware, SECRET)
        app.add_middleware(CSRFProtection, field_name="custom_csrf")

        @app.get("/csrf")
        def get_token():
            return {
                "automatic": csrf_token(),
                "explicit": csrf_token("custom_csrf"),
            }

        @app.post("/submit")
        def submit():
            return {"ok": True}

        client = app.test_client()
        tokens = client.get("/csrf").json
        self.assertEqual(tokens["automatic"], tokens["explicit"])
        response = client.post(
            "/submit",
            headers={"x-csrf-token": tokens["automatic"]},
        )
        self.assertEqual(response.status_code, 200)

    def test_password_hasher_rejects_self_incompatible_parameters(self):
        invalid_options = (
            {"n": 3},
            {"n": True},
            {"r": 0},
            {"p": 17},
            {"salt_bytes": 7},
            {"key_bytes": 129},
        )
        for options in invalid_options:
            with self.subTest(options=options), self.assertRaises(ValueError):
                PasswordHasher(**options)

        hasher = PasswordHasher(n=2**10, salt_bytes=8, key_bytes=16)
        encoded = hasher.hash("valid password")
        self.assertTrue(hasher.verify("valid password", encoded))
        self.assertFalse(hasher.needs_rehash(encoded))
        self.assertTrue(PasswordHasher(n=2**10, salt_bytes=9, key_bytes=16).needs_rehash(encoded))

    def test_oauth_state_requires_session_and_supports_multiple_tabs(self):
        oauth = OAuth2Client.google(
            client_id="client",
            client_secret="secret",
            redirect_uri="https://example.com/callback",
            secret_key=SECRET,
        )
        with self.assertRaisesRegex(RuntimeError, "SessionMiddleware"):
            oauth.authorization_url()

        app = self.make_app()
        app.add_middleware(SessionMiddleware, SECRET)

        @app.get("/oauth/start")
        def start():
            return {"url": oauth.authorization_url(**dict(request.args))}

        @app.get("/oauth/validate")
        def validate():
            try:
                oauth.validate_state(request.args["state"])
            except ValueError:
                return {"valid": False}, 400
            return {"valid": True}

        def start_state(client, query=""):
            url = client.get(f"/oauth/start{query}").json["url"]
            query = parse_qs(urlsplit(url).query)
            self.assertEqual(query["code_challenge_method"], ["S256"])
            return query["state"][0]

        first_client = app.test_client()
        first_state = start_state(first_client)
        second_state = start_state(first_client)
        foreign_state = start_state(app.test_client())
        protected_url = first_client.get(
            "/oauth/start?state=attacker&nonce=attacker&code_challenge=attacker"
        ).json["url"]
        protected_query = parse_qs(urlsplit(protected_url).query)
        self.assertNotEqual(protected_query["state"], ["attacker"])
        self.assertNotEqual(protected_query["nonce"], ["attacker"])
        self.assertNotEqual(protected_query["code_challenge"], ["attacker"])

        self.assertEqual(
            first_client.get(f"/oauth/validate?state={foreign_state}").status_code,
            400,
        )
        self.assertEqual(
            first_client.get(f"/oauth/validate?state={first_state}").status_code,
            200,
        )
        self.assertEqual(
            first_client.get(f"/oauth/validate?state={first_state}").status_code,
            400,
        )
        self.assertEqual(
            first_client.get(f"/oauth/validate?state={second_state}").status_code,
            200,
        )

    def test_oauth_token_exchange_uses_the_validated_pkce_verifier(self):
        oauth = OAuth2Client.google(
            client_id="client",
            client_secret="secret",
            redirect_uri="https://example.com/callback",
            secret_key=SECRET,
        )
        app = self.make_app()
        app.add_middleware(SessionMiddleware, SECRET)

        @app.get("/oauth/start")
        def start():
            return {"url": oauth.authorization_url()}

        @app.get("/oauth/callback")
        def callback():
            oauth.validate_state(request.args["state"])
            return oauth.fetch_token(request.args["code"])

        client = app.test_client()
        url = client.get("/oauth/start").json["url"]
        state = parse_qs(urlsplit(url).query)["state"][0]
        with patch.object(
            oauth,
            "_request_json",
            return_value={"access_token": "token"},
        ) as request_json:
            response = client.get(f"/oauth/callback?state={state}&code=code")

        self.assertEqual(response.json, {"access_token": "token"})
        sent_data = request_json.call_args.kwargs["data"]
        self.assertEqual(sent_data["code"], "code")
        self.assertTrue(sent_data["code_verifier"])

    def test_response_and_http_exception_validate_every_status_path(self):
        with self.assertRaises(ValueError):
            make_response(("invalid", 0))
        response = Response("ok")
        with self.assertRaises(ValueError):
            make_response(response, 700)
        self.assertEqual(response.status_code, 200)
        with self.assertRaises(ValueError):
            response.status_code = 700
        with self.assertRaises(ValueError):
            HTTPException(0)
        with self.assertRaises(ValueError):
            HTTPException(103)
        with self.assertRaises(ValueError):
            HTTPException(600)
        self.assertEqual(HTTPException(599, detail="").detail, "")

    def test_database_validates_types_and_supports_default_values(self):
        class OnlyId(Model):
            id = Integer(primary_key=True, autoincrement=True)

        class Values(Model):
            id = Integer(primary_key=True, autoincrement=True)
            count = Integer()
            ratio = Float()
            day = Date()

        database = Database(":memory:")
        database.create_all(OnlyId, Values)
        with database.session() as database_session:
            only_id = database_session.add(OnlyId())
            self.assertEqual(only_id.id, 1)
            query = database_session.query(OnlyId)
            self.assertIsInstance(query, Query)
            with self.assertRaises(TypeError):
                query.limit(1.5)
            with self.assertRaises(TypeError):
                query.offset(True)
            with self.assertRaises(TypeError):
                database_session.add(Values(count="1", ratio=1.0, day=date(2026, 8, 27)))
            with self.assertRaises(ValueError):
                database_session.add(Values(count=1, ratio=float("inf"), day=date(2026, 8, 27)))
            with self.assertRaises(TypeError):
                database_session.add(Values(count=1, ratio=1.0, day=datetime.now(timezone.utc)))

    def test_openapi_supports_modern_typing_primitives(self):
        class Payload(TypedDict):
            identifier: UUID
            created_at: datetime
            birthday: date
            tags: Sequence[str]
            metadata: Mapping[str, int]
            score: Annotated[int, {"minimum": 0}]

        schemas: dict[str, object] = {}
        self.assertEqual(schema_for(UUID), {"type": "string", "format": "uuid"})
        self.assertEqual(schema_for(date), {"type": "string", "format": "date"})
        self.assertEqual(
            schema_for(datetime),
            {"type": "string", "format": "date-time"},
        )
        self.assertEqual(schema_for(list), {"type": "array", "items": {}})
        self.assertEqual(
            schema_for(dict),
            {"type": "object", "additionalProperties": {}},
        )
        self.assertEqual(
            schema_for(tuple[int, str]),
            {
                "type": "array",
                "prefixItems": [{"type": "integer"}, {"type": "string"}],
                "minItems": 2,
                "maxItems": 2,
            },
        )
        self.assertEqual(schema_for(Payload, schemas), {"$ref": "#/components/schemas/Payload"})
        payload_schema = schemas["Payload"]
        self.assertIsInstance(payload_schema, dict)
        self.assertEqual(payload_schema["properties"]["score"]["minimum"], 0)
        self.assertEqual(
            payload_schema["properties"]["metadata"],
            {"type": "object", "additionalProperties": {"type": "integer"}},
        )
        self.assertEqual(
            payload_schema["required"],
            ["birthday", "created_at", "identifier", "metadata", "score", "tags"],
        )

    def test_make_response_preserves_duplicate_sequence_headers(self) -> None:
        from higuma import Response, make_response

        response = make_response(
            ("ok", 201, [("Set-Cookie", "a=1"), ("Set-Cookie", "b=2"), ("X-Tag", "one")])
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            [value for name, value in response.header_items if name == "set-cookie"],
            ["a=1", "b=2"],
        )
        self.assertEqual(response.headers["x-tag"], "one")

        two_tuple = make_response(("ok", [("X-Dup", "1"), ("X-Dup", "2")]))
        self.assertEqual(two_tuple.status_code, 200)
        self.assertEqual(
            [value for name, value in two_tuple.header_items if name == "x-dup"],
            ["1", "2"],
        )

        overridden = make_response(
            (Response("ok", headers={"x-a": "0"}), {"x-a": "1"}),
            headers={"x-a": "2"},
        )
        self.assertEqual(overridden.headers["x-a"], "2")

    def test_sse_encode_follows_whatwg_line_breaks(self) -> None:
        from higuma import ServerSentEvent

        self.assertEqual(
            ServerSentEvent(data="hello\n").encode(),
            b"data: hello\ndata: \n\n",
        )
        self.assertEqual(
            ServerSentEvent(data="a\r\nb\rc\nd").encode(),
            b"data: a\ndata: b\ndata: c\ndata: d\n\n",
        )
        unicode_sep = "line1\u2028line2"
        self.assertEqual(
            ServerSentEvent(data=unicode_sep).encode(),
            f"data: {unicode_sep}\n\n".encode(),
        )
        self.assertEqual(
            ServerSentEvent(data="", comment="c1\n").encode(),
            b": c1\n: \ndata: \n\n",
        )

    def test_database_session_save_enforces_not_null_fields(self) -> None:
        from higuma import Database, Integer, Model, String

        class Item(Model):
            __tablename__ = "items_null_check"
            id = Integer(primary_key=True)
            name = String(nullable=False)

        db = Database("sqlite:///:memory:")
        db.create_all(Item)
        with db.session() as db_session:
            item = db_session.add(Item(name="bear"))
            db_session.commit()
            item.name = None
            with self.assertRaises(ValueError):
                db_session.save(item)

    def test_streaming_case_insensitive_length_file_disposition_and_orm_edge_cases(self) -> None:
        import tempfile
        from pathlib import Path

        from higuma import Database, FileResponse, Integer, Model, StreamingResponse, String

        stream = StreamingResponse([b"a"], headers={"CONTENT-LENGTH": "999"})
        self.assertNotIn("content-length", stream.headers)
        stream._close()

        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "note.txt"
            sample.write_text("hi", encoding="utf-8")
            response = FileResponse(sample, filename="dir/report.txt", as_attachment=True)
            self.assertIn(
                "filename*=UTF-8''dir%2Freport.txt", response.headers["content-disposition"]
            )

        with self.assertRaises(ValueError):
            String(length=0)
        with self.assertRaises(ValueError):
            String(length=True)

        calls: list[int] = []

        def make_code() -> str:
            calls.append(1)
            return f"c{len(calls)}"

        class Entry(Model):
            __tablename__ = "entries_edge_check"
            id = Integer(primary_key=True)
            code = String(default=make_code, nullable=False)

        explicit = Entry(code="fixed")
        self.assertEqual(calls, [])
        self.assertEqual(explicit.code, "fixed")
        self.assertEqual(calls, [])

        db = Database("sqlite:///:memory:")
        db.create_all(Entry)
        with db.session() as db_session:
            db_session.add(explicit)
            db_session.commit()
            self.assertIsNone(db_session.query(Entry).limit(0).first())
            with self.assertRaises(ValueError):
                db_session.delete(Entry(code="unsaved"))
        db.close()
        self.assertIsNone(db._memory_connection)

        broken = Database("sqlite:///:memory:")
        broken.connect = lambda: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[method-assign]
        with self.assertRaises(RuntimeError), broken.session():
            pass
        # Lock must not remain held after connect() fails.
        self.assertTrue(broken._memory_lock.acquire(blocking=False))
        broken._memory_lock.release()

    def test_core_add_route_is_atomic_on_duplicate_method(self) -> None:
        from higuma._core import HigumaCore

        core = HigumaCore()
        core.add_route("/atomic", ["POST"], lambda req: "post")
        with self.assertRaises(ValueError):
            core.add_route("/atomic", ["GET", "POST"], lambda req: "both")
        # GET must not have been partially registered when POST failed.
        core.add_route("/atomic", ["GET"], lambda req: "get")

    def test_cookie_streaming_sse_run_and_supervisor_validation(self) -> None:
        import signal
        from datetime import timedelta

        from higuma import EventSourceResponse, Higuma, Response, StreamingResponse, Supervisor
        from higuma.supervisor import _free_ports

        response = Response("ok")
        response.set_cookie("session", "abc", max_age=timedelta(minutes=5))
        self.assertIn("Max-Age=300", response.header_items[-1][1])
        with self.assertRaises(ValueError):
            response.set_cookie("bad", "1", max_age=True)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            response.set_cookie("bad", "1", path="/; HttpOnly")
        with self.assertRaises(ValueError):
            response.set_cookie("bad", "1", domain="example.com; Secure")

        closed_flags: list[str] = []

        class FailingIterator:
            def __iter__(self):
                return self

            def __next__(self):
                raise StopIteration

            def close(self) -> None:
                closed_flags.append("iter")
                raise RuntimeError("iter close boom")

        class ClosingIterable:
            def __iter__(self):
                return FailingIterator()

            def close(self) -> None:
                closed_flags.append("content")

        stream = StreamingResponse(ClosingIterable())
        with self.assertRaises(RuntimeError):
            stream._close()
        self.assertEqual(closed_flags, ["iter", "content"])

        sse = EventSourceResponse(
            iter(["hello"]),
            headers=[
                ("Cache-Control", "private"),
                ("Content-Length", "99"),
                ("X-Tag", "a"),
                ("X-Tag", "b"),
            ],
        )
        self.assertEqual(sse.headers["cache-control"], "private")
        self.assertNotIn("content-length", sse.headers)
        self.assertEqual(
            [v for k, v in sse.header_items if k == "x-tag"],
            ["a", "b"],
        )

        started: list[bool] = []
        app = Higuma(__name__, static_folder=None)
        app.on_startup(lambda: started.append(True))
        for bad_port in (-1, 70000, True):
            with self.assertRaises(ValueError):
                app.run(port=bad_port)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            app.run(workers=True)  # type: ignore[arg-type]
        self.assertEqual(started, [])

        for bad_kwargs in (
            {"app": ""},
            {"app": "--evil"},
            {"app": "mod:app", "port": 70000},
            {"app": "mod:app", "processes": True},
            {"app": "mod:app", "startup_timeout": float("nan")},
            {"app": "mod:app", "restart_window": float("inf")},
        ):
            with self.assertRaises(ValueError):
                Supervisor(**bad_kwargs)  # type: ignore[arg-type]

        ports = _free_ports(3)
        self.assertEqual(len(set(ports)), 3)

        # Signal handlers must be restored if socket.create_server fails in Supervisor._serve.
        before_sigint = signal.getsignal(signal.SIGINT)
        sup = Supervisor("mod:app", host="256.256.256.256", port=8000, processes=2)
        with self.assertRaises(OSError):
            sup._serve([1, 2])
        self.assertIs(signal.getsignal(signal.SIGINT), before_sigint)

    def test_routing_rejects_duplicates_huge_ints_inf_floats_and_bad_multipart_ct(self) -> None:
        from higuma import Higuma, request
        from higuma._core import HigumaCore

        app = Higuma(__name__, static_folder=None)
        with self.assertRaises(ValueError):

            @app.get("/users/<int:id>/posts/<int:id>")
            def dup(id: int):
                return {"id": id}

        core = HigumaCore()
        with self.assertRaises(ValueError):
            core.add_route("/users/<int:id>/posts/<int:id>", ["GET"], lambda req: "dup")
        with self.assertRaises(ValueError):
            core.add_route("/users/<int:1bad>", ["GET"], lambda req: "bad")

        @app.get("/items/<int:item_id>")
        def get_item(item_id: int):
            return {"id": item_id}

        @app.get("/price/<float:amount>")
        def get_price(amount: float):
            return {"amount": amount}

        @app.post("/upload")
        def upload():
            return {"keys": list(request.form.keys())}

        client = app.test_client()
        self.assertEqual(client.get("/items/" + "9" * 5000).status_code, 404)
        self.assertEqual(client.get("/price/" + "9" * 400).status_code, 404)
        self.assertEqual(client.get("/items/42").json, {"id": 42})
        self.assertEqual(client.get("/price/12.5").json, {"amount": 12.5})

        from types import SimpleNamespace

        from higuma.exceptions import BadRequest
        from higuma.multipart import parse_multipart

        with self.assertRaises(BadRequest):
            parse_multipart(SimpleNamespace(content_type="multipart/form-data; boundary=熊"))

        bad_ct = client.post(
            "/upload",
            data=b"--b--\r\n",
            headers={"content-type": "multipart/form-data; boundary=\xff"},
        )
        self.assertEqual(bad_ct.status_code, 400)

    def test_request_proxy_setattr_isolation_orm_fields_and_config_prefix(self) -> None:
        from higuma import Blob, Boolean, Config, Database, Higuma, Integer, Model, request

        app = Higuma(__name__, static_folder=None)

        @app.before_request
        def attach():
            if "tenant" in request.args:
                request.tenant = request.args["tenant"]

        @app.get("/tenant")
        def tenant():
            return {"tenant": getattr(request, "tenant", None)}

        client = app.test_client()
        self.assertEqual(client.get("/tenant?tenant=acme").json, {"tenant": "acme"})
        # Must never leak onto the global request LocalProxy across requests.
        self.assertEqual(client.get("/tenant").json, {"tenant": None})
        self.assertNotIn("tenant", request.__dict__)

        class Record(Model):
            id = Integer(primary_key=True, autoincrement=True)
            flag = Boolean(nullable=False)
            payload = Blob(nullable=True)

        db = Database("sqlite:///:memory:")
        db.create_all(Record)
        with db.session() as db_session:
            with self.assertRaises(TypeError):
                db_session.add(Record(flag=1.0, payload=b"ok"))
            with self.assertRaises(TypeError):
                db_session.add(Record(flag=True, payload=1024))
            saved = db_session.add(Record(flag=True, payload=bytearray(b"hi")))
            db_session.commit()
            loaded = db_session.query(Record).get(saved.id)
            assert loaded is not None
            self.assertEqual(loaded.payload, b"hi")
        db.close()

        with self.assertRaises(ValueError):
            Config().from_prefixed_env("")

    def test_app_blueprint_limits_validation_atomic_mounts_and_multipart_escaping(self) -> None:
        from higuma import Blueprint, Higuma, HTTPException, MethodView, request, url_for

        with self.assertRaises(ValueError):
            Higuma(__name__, max_content_length=True)
        with self.assertRaises(ValueError):
            Higuma(__name__, request_memory_limit=True)

        app = Higuma(__name__, static_folder=None)

        with self.assertRaises(TypeError):
            app.route("/bad-method", methods="POST")(lambda: "x")
        with self.assertRaises(TypeError):
            app.get("/bad-tag", tags="users")(lambda: "x")
        with self.assertRaises(ValueError):
            app.route("/empty-methods", methods=[])(lambda: "x")
        with self.assertRaises(TypeError):
            app.websocket("/bad-ws", allowed_origins="https://example.com")(lambda ws: None)

        bp = Blueprint("api", __name__, url_prefix="/api")
        with self.assertRaises(TypeError):
            bp.route("/bad-method", methods="POST")(lambda: "x")
        with self.assertRaises(TypeError):
            bp.get("/bad-tag", tags="items")(lambda: "x")
        with self.assertRaises(ValueError):
            bp.route("/empty-methods", methods=())(lambda: "x")
        with self.assertRaises(TypeError):
            bp.websocket("/bad-ws", allowed_origins="https://example.com")(lambda ws: None)

        class ItemMethodView(MethodView):
            def post(self):
                return {"created": True}

        bp.route("/items")(ItemMethodView.as_view("items"))
        app.register_blueprint(bp)

        @app.get("/home")
        def home():
            return "ok"

        app.config["MAX_FORM_PARTS"] = 1
        app.config["MAX_FORM_MEMORY_SIZE"] = 32
        app.config["UPLOAD_MEMORY_LIMIT"] = 16
        with app.test_request_context(
            "/upload",
            method="POST",
            data={"a": "1", "b": "2"},
            files={"doc": ("hello.txt", b"hi", "text/plain")},
        ):
            self.assertEqual(request.max_form_parts, 1)
            self.assertEqual(request.max_form_memory_size, 32)
            self.assertEqual(request.upload_memory_limit, 16)
            with self.assertRaises(HTTPException) as exc:
                _ = request.form
            self.assertEqual(exc.exception.status_code, 413)

        app.config["MAX_FORM_PARTS"] = 100
        app.config["MAX_FORM_MEMORY_SIZE"] = 1024 * 1024
        app.config["UPLOAD_MEMORY_LIMIT"] = 1024 * 1024
        with app.test_request_context("/home", base_url="http://example.com"):
            self.assertEqual(
                url_for("home", _external=True, _scheme="HTTPS"),
                "https://example.com/home",
            )

        @app.get("/conflict-path", endpoint="legacy_path")
        def conflict_path():
            return "taken"

        def dummy_wsgi(environ, start_response):
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"wsgi"]

        with self.assertRaises(ValueError):
            app.mount_wsgi("/legacy", dummy_wsgi, name="legacy")
        self.assertNotIn("legacy_root", app._endpoint_rules)

        async def dummy_asgi(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"asgi"})

        with self.assertRaises(ValueError):
            app.mount_asgi("/<path:bad>", dummy_asgi, name="bad_asgi")
        self.assertNotIn("bad_asgi_root", app._endpoint_rules)

        @app.post("/echo-upload")
        def echo_upload():
            uploaded = request.files['file"1']
            return {
                "field": request.form['note"1\\x'],
                "filename": uploaded.filename,
                "body": uploaded.read().decode("utf-8"),
            }

        client = app.test_client()
        self.assertEqual(client.post("/api/items").json, {"created": True})
        self.assertEqual(client.get("/api/items").status_code, 405)

        upload_resp = client.post(
            "/echo-upload",
            data={'note"1\\x': "hello"},
            files={'file"1': ('quote"name.txt', b"payload", "text/plain")},
        )
        self.assertEqual(upload_resp.status_code, 200)
        self.assertEqual(
            upload_resp.json,
            {"field": "hello", "filename": "quote_name.txt", "body": "payload"},
        )

        with self.assertRaises(ValueError):
            client.post(
                "/echo-upload",
                data={"bad\r\nname": "v"},
                files={"doc": ("ok.txt", b"x", "text/plain")},
            )
        with self.assertRaises(ValueError):
            client.post(
                "/echo-upload",
                files={"doc": ("bad\r\n.txt", b"x", "text/plain")},
            )
        with self.assertRaises(ValueError):
            client.post(
                "/echo-upload",
                files={"doc": ("ok.txt", b"x", "text/plain\r\nX-Injected: 1")},
            )

    def test_signals_response_headers_request_close_body_ws_and_cli_hardening(self) -> None:
        import asyncio
        from dataclasses import dataclass
        from uuid import UUID

        from higuma import (
            Higuma,
            RedirectResponse,
            Request,
            Response,
            Signal,
            StreamingResponse,
            WebSocket,
        )
        from higuma.body import BodyBuffer, store_chunk
        from higuma.exceptions import RequestEntityTooLarge
        from higuma.request import MultiDict, UploadFile

        # Signal value-typed senders and callable async receiver rejection
        sig = Signal("orders")
        seen: list[object] = []

        def on_order(sender, **kw):
            seen.append((sender, kw.get("n")))

        sig.connect(on_order, sender="order-topic")
        sig.connect(on_order, sender=100001)
        sig.send(b"order-topic".decode("ascii"), n=1)
        sig.send(int("100001"), n=2)
        sig.send(True, n=3)
        self.assertEqual(seen, [("order-topic", 1), (100001, 2)])
        self.assertTrue(sig.disconnect(on_order, sender=b"order-topic".decode("ascii")))
        self.assertTrue(sig.disconnect(on_order, sender=int("100001")))
        self.assertFalse(sig.has_receivers_for("order-topic"))

        class AsyncCallable:
            async def __call__(self, sender, **kw):
                return None

        with self.assertRaises(TypeError):
            sig.connect(AsyncCallable())

        # Duplicate tuple-pair headers across Response, RedirectResponse, StreamingResponse
        pairs = [("Set-Cookie", "a=1"), ("Set-Cookie", "b=2")]
        resp = Response("ok", headers=pairs)
        self.assertEqual([v for k, v in resp.header_items if k == "set-cookie"], ["a=1", "b=2"])

        redir = RedirectResponse("/next", headers=[("Location", "/ignored"), *pairs])
        self.assertEqual(redir.headers["location"], "/next")
        self.assertEqual([v for k, v in redir.header_items if k == "set-cookie"], ["a=1", "b=2"])
        self.assertEqual([v for k, v in redir.header_items if k == "location"], ["/next"])

        stream = StreamingResponse(
            [b"hi"],
            headers=[("Content-Length", "999"), *pairs],
            content_length=2,
        )
        self.assertEqual(stream.headers["content-length"], "2")
        self.assertEqual([v for k, v in stream.header_items if k == "content-length"], ["2"])
        self.assertEqual([v for k, v in stream.header_items if k == "set-cookie"], ["a=1", "b=2"])
        stream._close()

        # Request.iter_body/aiter_body bool rejection & resilient Request.close()
        req = Request({"body": b"abc"})
        with self.assertRaises(ValueError):
            list(req.iter_body(True))

        async def bad_aiter():
            async for _ in req.aiter_body(True):
                pass

        with self.assertRaises(ValueError):
            asyncio.run(bad_aiter())

        closed_log: list[str] = []

        class FailingStream:
            def close(self):
                closed_log.append("upload1")
                raise OSError("boom")

        class GoodStream:
            def __init__(self, name: str):
                self.name = name

            def close(self):
                closed_log.append(self.name)

        req_close = Request({"body": b""})
        req_close._body_file = GoodStream("body")
        req_close._files_cache = MultiDict(
            [
                ("f", UploadFile("a.txt", stream=FailingStream())),
                ("f", UploadFile("b.txt", stream=GoodStream("upload2"))),
            ]
        )
        with self.assertRaises(OSError):
            req_close.close()
        self.assertEqual(closed_log, ["upload1", "upload2", "body"])

        # BodyBuffer bool rejection & store_chunk fast oversize check
        with self.assertRaises(ValueError):
            BodyBuffer(True)
        with self.assertRaises(ValueError):
            BodyBuffer(10, memory_limit=True)
        buf = BodyBuffer(4, memory_limit=2)
        with self.assertRaises(RequestEntityTooLarge):
            asyncio.run(store_chunk(buf, b"12345"))
        self.assertFalse(buf.rolled_to_disk)
        buf.close()

        # WebSocket payload validation, asend, and structured send_json/asend_json
        sent: list[tuple[str, object]] = []

        class DummySession:
            def send_text(self, text: str) -> None:
                sent.append(("text", text))

            def send_bytes(self, data: bytes) -> None:
                sent.append(("bytes", data))

        ws = WebSocket(DummySession())
        with self.assertRaises(TypeError):
            ws.send(5)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            ws.send_bytes(5)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            asyncio.run(ws.asend(5))  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            asyncio.run(ws.asend_bytes(5))  # type: ignore[arg-type]

        @dataclass
        class Event:
            id: UUID
            label: str

        event = Event(UUID("12345678-1234-5678-1234-567812345678"), "bear")
        ws.send_json(event)
        asyncio.run(ws.asend("hi"))
        asyncio.run(ws.asend(b"ok"))
        asyncio.run(ws.asend_json(event))
        self.assertEqual(
            sent,
            [
                ("text", '{"id":"12345678-1234-5678-1234-567812345678","label":"bear"}'),
                ("text", "hi"),
                ("bytes", b"ok"),
                ("text", '{"id":"12345678-1234-5678-1234-567812345678","label":"bear"}'),
            ],
        )

        # CLI bool default validation & CliRunner malformed quote handling
        cli_app = Higuma(__name__, static_folder=None)
        with self.assertRaises(TypeError):

            @cli_app.cli.command()
            def bad_flag(dry_run: bool = "false"):  # type: ignore[assignment]
                return dry_run

        result = cli_app.test_cli_runner().invoke("routes 'unterminated")
        self.assertEqual(result.exit_code, 1)
        self.assertIsInstance(result.exception, ValueError)

    def test_middleware_security_session_ior_flash_filter_and_exception_hardening(self) -> None:
        from higuma import (
            AsyncSQLAlchemyExtension,
            CSRFProtection,
            Higuma,
            HTTPException,
            PasswordHasher,
            ProxyHeadersMiddleware,
            SecurityHeadersMiddleware,
            SessionMiddleware,
            SQLAlchemyExtension,
            TokenSigner,
            TrustedHostMiddleware,
            abort,
            flash,
            get_flashed_messages,
            request,
            session,
            validate_user_id,
        )

        # TrustedHostMiddleware: reject port 0, canonicalize IPv6 & bracketed patterns
        host_app = Higuma(__name__, static_folder=None)
        host_app.add_middleware(TrustedHostMiddleware, ["[::1]", "example.test"])

        @host_app.get("/ping")
        def ping():
            return {"ok": True}

        host_client = host_app.test_client()
        self.assertEqual(
            host_client.get("/ping", headers={"host": "[0:0:0:0:0:0:0:1]:8000"}).status_code, 200
        )
        self.assertEqual(host_client.get("/ping", headers={"host": "[::1]"}).status_code, 200)
        self.assertEqual(
            host_client.get("/ping", headers={"host": "example.test:0"}).status_code, 403
        )
        with self.assertRaises(TypeError):
            TrustedHostMiddleware(bytearray(b"example.test"))  # type: ignore[arg-type]

        # SecurityHeadersMiddleware: None/empty disables X-Frame-Options & Referrer-Policy cleanly
        sec_app = Higuma(__name__, static_folder=None)
        sec_app.add_middleware(
            SecurityHeadersMiddleware,
            frame_options=None,
            referrer_policy=None,
        )

        @sec_app.get("/sec")
        def sec():
            return "ok"

        sec_resp = sec_app.test_client().get("/sec")
        self.assertEqual(sec_resp.status_code, 200)
        self.assertNotIn("x-frame-options", sec_resp.headers)
        self.assertNotIn("referrer-policy", sec_resp.headers)

        # ProxyHeadersMiddleware: IPv4-mapped IPv6 peer matches IPv4 trusted_proxies
        proxy_mw = ProxyHeadersMiddleware(["127.0.0.1"])
        from higuma import Request, Response

        mapped_req = Request(
            {
                "client_addr": "::ffff:127.0.0.1",
                "headers": {
                    "x-forwarded-for": "203.0.113.42",
                    "x-forwarded-proto": "https",
                },
            }
        )
        proxy_mw(mapped_req, lambda req: Response("ok"))
        self.assertEqual(mapped_req.client_addr, "203.0.113.42")
        self.assertEqual(mapped_req.scheme, "https")

        # Security helpers: CSRFProtection safe_methods, TokenSigner, PasswordHasher, validate_user_id
        with self.assertRaises(TypeError):
            CSRFProtection(safe_methods="GET")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            CSRFProtection(header_name="")

        signer = TokenSigner("a" * 32)
        with self.assertRaises(ValueError):
            signer.loads(None)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            signer.loads(123)  # type: ignore[arg-type]

        hasher = PasswordHasher(n=2**10)
        with self.assertRaises(ValueError):
            hasher.hash(None)  # type: ignore[arg-type]
        self.assertFalse(hasher.verify(None, "higuma$scrypt$1024$8$1$aa$bb"))  # type: ignore[arg-type]
        self.assertFalse(hasher.verify("pw", None))  # type: ignore[arg-type]
        self.assertTrue(hasher.needs_rehash(None))  # type: ignore[arg-type]
        self.assertFalse(validate_user_id(None))  # type: ignore[arg-type]
        self.assertFalse(validate_user_id(12345))  # type: ignore[arg-type]

        # _SessionProxy |= and get_flashed_messages category_filter bare string rejection
        sess_app = Higuma(__name__, static_folder=None)
        sess_app.add_middleware(SessionMiddleware, "s" * 32)

        def apply_merge(store) -> None:
            store |= {"role": "admin", "count": 2}

        @sess_app.post("/merge")
        def merge():
            apply_merge(session)
            flash("saved", "info")
            return {"role": session["role"], "count": session["count"]}

        @sess_app.get("/flashes")
        def flashes():
            if request.args.get("bad"):
                return {"items": get_flashed_messages(category_filter="info")}  # type: ignore[arg-type]
            return {"items": get_flashed_messages(category_filter=("info",))}

        sess_client = sess_app.test_client()
        self.assertEqual(sess_client.post("/merge").json, {"role": "admin", "count": 2})
        self.assertEqual(sess_client.get("/flashes").json, {"items": ["saved"]})
        with self.assertRaises(TypeError), sess_app.test_request_context("/flashes"):
            get_flashed_messages(category_filter="info")

        # HTTPException / abort reject bool/float status codes
        for bad_status in (True, 400.9, "404"):
            with self.assertRaises(ValueError):
                HTTPException(bad_status)  # type: ignore[arg-type]
            with self.assertRaises(ValueError):
                abort(bad_status)  # type: ignore[arg-type]

        # SQLAlchemyExtension / AsyncSQLAlchemyExtension reject blank/non-str URLs upfront
        db_app = Higuma(__name__, static_folder=None)
        for bad_url in ("", "   ", None, 123):
            with self.assertRaises(ValueError):
                SQLAlchemyExtension().init_app(db_app, bad_url)  # type: ignore[arg-type]
            with self.assertRaises(ValueError):
                AsyncSQLAlchemyExtension().init_app(db_app, bad_url)  # type: ignore[arg-type]

    def test_mounts_multipart_database_observability_and_staticfiles_hardening(self) -> None:
        import asyncio
        import json
        import logging
        import sys
        import tempfile
        from uuid import UUID

        from higuma import (
            Database,
            HealthChecks,
            Higuma,
            Integer,
            JSONLogFormatter,
            Model,
            Request,
            StaticFiles,
            String,
        )
        from higuma.database import quote_identifier
        from higuma.exceptions import BadRequest
        from higuma.mounts import _mounted_path, _server_address, wsgi_view
        from higuma.multipart import parse_multipart

        # WSGI mount PEP 3333 start_response error recovery before vs after body emission
        def recover_wsgi(environ, start_response):
            start_response("200 OK", [("Content-Type", "text/plain")])
            try:
                raise RuntimeError("early failure")
            except RuntimeError:
                start_response(
                    "503 Service Unavailable",
                    [("Content-Type", "text/plain")],
                    sys.exc_info(),
                )
            return [b"recovered"]

        recovered = wsgi_view(recover_wsgi, "/sub")(Request({"path": "/sub/x"}))
        self.assertEqual(recovered.status_code, 503)
        self.assertEqual(recovered.body, b"recovered")

        def late_error_wsgi(environ, start_response):
            write = start_response("200 OK", [("Content-Type", "text/plain")])
            write(b"partial")
            try:
                raise ValueError("late failure")
            except ValueError:
                start_response(
                    "500 Internal Server Error",
                    [("Content-Type", "text/plain")],
                    sys.exc_info(),
                )
            return []

        with self.assertRaises(ValueError):
            wsgi_view(late_error_wsgi, "/sub")(Request({"path": "/sub/x"}))

        def double_start_wsgi(environ, start_response):
            start_response("200 OK", [])
            start_response("201 Created", [])
            return [b"x"]

        with self.assertRaises(RuntimeError):
            wsgi_view(double_start_wsgi, "/sub")(Request({"path": "/sub/x"}))

        self.assertEqual(_mounted_path("/api%2Fsecret", "/api"), "/api%2Fsecret")
        self.assertEqual(_server_address("example.test:0", "https"), ("example.test", "443"))
        self.assertEqual(_server_address("example.test:99999", "http"), ("example.test", "80"))
        self.assertEqual(_server_address("", "http"), ("localhost", "80"))

        # Multipart malformed trailing delimiter after max_form_parts -> 400 (not 413)
        bad_trailing_body = b'--b1\r\nContent-Disposition: form-data; name="a"\r\n\r\n1\r\n--b1XX'
        req_trailing = Request(
            {
                "body": bad_trailing_body,
                "headers": {"Content-Type": "multipart/form-data; boundary=b1"},
            }
        )
        req_trailing.max_form_parts = 1
        with self.assertRaises(BadRequest):
            parse_multipart(req_trailing)

        # Database: refuse limit/offset on delete, validate Model types & URLs, preserve error
        class Item(Model):
            id = Integer(primary_key=True, autoincrement=True)
            name = String(nullable=False)

        with self.assertRaises(ValueError):
            Database(None)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            quote_identifier(123)  # type: ignore[arg-type]

        db = Database("sqlite:///:memory:")
        with self.assertRaises(TypeError):
            db.create_all(Item(name="not-a-class"))  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            db.drop_all(Model)
        db.create_all(Item)
        with db.session() as db_session:
            with self.assertRaises(TypeError):
                db_session.add({"name": "bad"})  # type: ignore[arg-type]
            with self.assertRaises(TypeError):
                db_session.save({"name": "bad"})  # type: ignore[arg-type]
            with self.assertRaises(TypeError):
                db_session.delete({"name": "bad"})  # type: ignore[arg-type]
            with self.assertRaises(TypeError):
                db_session.query(Item(name="x"))  # type: ignore[arg-type]
            db_session.add(Item(name="one"))
            db_session.add(Item(name="two"))
            db_session.commit()
            with self.assertRaises(ValueError):
                db_session.query(Item).filter_by(name="one").limit(1).delete()
            with self.assertRaises(ValueError):
                db_session.query(Item).offset(1).delete_all()
            self.assertEqual(db_session.query(Item).count(), 2)

        with self.assertRaisesRegex(RuntimeError, "original failure"), db.session():
            db.close()
            raise RuntimeError("original failure")
        db.close()

        # Observability: JSONLogFormatter default=str + exc_info; HealthChecks async callable probe
        formatter = JSONLogFormatter()
        try:
            raise ValueError("structured boom")
        except ValueError:
            record = logging.LogRecord(
                "higuma.test",
                logging.ERROR,
                __file__,
                1,
                "failed",
                (),
                sys.exc_info(),
            )
            record.higuma = {"id": UUID("12345678-1234-5678-1234-567812345678")}
        formatted = json.loads(formatter.format(record))
        self.assertEqual(formatted["id"], "12345678-1234-5678-1234-567812345678")
        self.assertIn("ValueError: structured boom", formatted["exception"])

        class AsyncProbe:
            async def __call__(self) -> bool:
                await asyncio.sleep(0)
                return True

        health_app = Higuma(__name__, static_folder=None)
        checks = HealthChecks()
        checks.add_check("callable_async", AsyncProbe())
        checks.install(health_app)
        self.assertEqual(health_app.test_client().get("/health/ready").status_code, 200)

        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            StaticFiles(tmp, index_file=123)  # type: ignore[arg-type]

    def test_app_blueprint_testing_and_websocket_validation(self) -> None:
        from http import HTTPStatus

        from higuma import Blueprint, Request, Response, StreamingResponse
        from higuma.websocket import WebSocket

        app = Higuma(__name__, static_folder=None, debug=True)

        # Declared status_code must be preserved for (body, headers) 2-tuples
        @app.post("/created-headers", status_code=201)
        def created_headers():
            return {"ok": True}, {"x-created": "yes"}

        @app.post("/created-header-list", status_code=202)
        def created_header_list():
            return "accepted", [("x-tag", "a"), ("x-tag", "b")]

        @app.get("/no-content-head")
        def no_content_head():
            return Response(b"", 204)

        @app.get("/not-modified-head")
        def not_modified_head():
            return Response(b"", 304)

        client = app.test_client()
        created = client.post("/created-headers")
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.headers["x-created"], "yes")
        self.assertEqual(created.json, {"ok": True})

        accepted = client.post("/created-header-list")
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(
            [value for key, value in accepted.header_items if key == "x-tag"],
            ["a", "b"],
        )

        # HEAD on 204 / 304 must not inject Content-Length
        head_204 = client.head("/no-content-head")
        self.assertEqual(head_204.status_code, 204)
        self.assertNotIn("content-length", head_204.headers)
        head_304 = client.head("/not-modified-head")
        self.assertEqual(head_304.status_code, 304)
        self.assertNotIn("content-length", head_304.headers)

        # TestClient / ASGITestClient reject invalid/zero ports in base_url
        for bad_url in (
            "http://localhost:0",
            "http://localhost:99999",
            "http://localhost:abc",
            123,
        ):
            with self.assertRaises(ValueError):
                client.get("/", base_url=bad_url)  # type: ignore[arg-type]
            with self.assertRaises(ValueError):
                app.test_async_client(base_url=bad_url)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            app.test_async_client(root_path=123)  # type: ignore[arg-type]

        # TestClient._materialize always closes StreamingResponse when chunk iteration fails
        closed = []

        def failing_stream():
            try:
                yield b"first"
                raise RuntimeError("stream boom")
            finally:
                closed.append(True)

        with self.assertRaisesRegex(RuntimeError, "stream boom"):
            client._materialize(StreamingResponse(failing_stream()))
        self.assertEqual(closed, [True])

        # Debug _handle_exception formats the provided error's traceback even outside except
        try:
            raise RuntimeError("detached failure")
        except RuntimeError as captured:
            detached_error = captured
        debug_response = app._handle_exception(
            detached_error, Request({"method": "GET", "path": "/"})
        )
        self.assertEqual(debug_response.status_code, 500)
        self.assertIn("RuntimeError: detached failure", debug_response.text)

        # Error handler registration validates status codes, exception types, and callability
        bp = Blueprint("api", __name__)
        for invalid_status in (99, 199, 600):
            with self.assertRaises(ValueError):
                app.errorhandler(invalid_status)
            with self.assertRaises(ValueError):
                bp.errorhandler(invalid_status)
            with self.assertRaises(ValueError):
                bp.app_errorhandler(invalid_status)
        for invalid_key in (True, False, "404", ValueError("instance"), object):
            with self.assertRaises(TypeError):
                app.register_error_handler(invalid_key, lambda err: ("err", 500))  # type: ignore[arg-type]
            with self.assertRaises(TypeError):
                bp.register_error_handler(invalid_key, lambda err: ("err", 500))  # type: ignore[arg-type]
            with self.assertRaises(TypeError):
                bp.app_errorhandler(invalid_key)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            app.register_error_handler(404, "not-callable")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            bp.register_error_handler(404, "not-callable")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            bp.app_errorhandler(404)("not-callable")  # type: ignore[arg-type]
        # HTTPStatus IntEnum continues to work cleanly
        app.register_error_handler(HTTPStatus.IM_A_TEAPOT, lambda err: ("teapot", 418))
        self.assertIn(418, app._error_handlers)

        # Middleware and Blueprint registration validation
        with self.assertRaises(TypeError):
            app.middleware("not-callable")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            app.add_middleware("not-callable")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            Blueprint(123, __name__)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            bp.register_blueprint(object())  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            bp.register_blueprint(Blueprint("child", __name__), name=123)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            app.register_blueprint(object())  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            app.register_blueprint(bp, name_prefix=123)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            app.register_blueprint(bp, name=123)  # type: ignore[arg-type]

        # WebSocket.send_text / asend_text reject non-str payloads consistently
        class DummySession:
            def send_text(self, text: str) -> None:
                pass

        ws = WebSocket(DummySession())
        with self.assertRaises(TypeError):
            ws.send_text(b"bytes")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            asyncio.run(ws.asend_text(123))  # type: ignore[arg-type]

    def test_context_routing_views_security_redis_frontend_config_and_supervisor(self) -> None:
        import json
        import tempfile
        from enum import IntEnum
        from pathlib import Path
        from uuid import UUID

        from higuma import (
            BadRequest,
            Config,
            MethodView,
            RateLimitMiddleware,
            RedisRateLimitMiddleware,
            RedisSessionMiddleware,
            RedisSessionStore,
            Request,
            SessionMiddleware,
            Supervisor,
            TokenSigner,
            View,
            ViteManifest,
            after_this_request,
            csrf_token,
            g,
        )

        app = Higuma(__name__, static_folder=None)

        # g.pop() matches Flask 3.1: raises KeyError without default, returns default when provided
        with app.app_context():
            self.assertIsNone(g.pop("missing", None))
            self.assertEqual(g.pop("missing", "fallback"), "fallback")
            with self.assertRaises(KeyError):
                g.pop("missing")
            g.token = "abc"
            self.assertEqual(g.pop("token"), "abc")

        # after_this_request rejects non-callables immediately
        with app.test_request_context("/"), self.assertRaises(TypeError):
            after_this_request("not-callable")  # type: ignore[arg-type]

        # url_for / Rule.build validates typed path parameters and rejects None
        class Kind(IntEnum):
            BEAR = 7

        @app.get("/users/<int:user_id>/ratio/<float:ratio>/item/<uuid:item_id>")
        def typed_item(user_id: int, ratio: float, item_id: UUID):
            return {"id": user_id, "ratio": ratio, "item": str(item_id)}

        uid = UUID("12345678-1234-5678-1234-567812345678")
        self.assertEqual(
            app.url_for("typed_item", user_id=Kind.BEAR, ratio=1.5, item_id=uid),
            f"/users/7/ratio/1.5/item/{uid}",
        )
        for bad_kwargs in (
            {"user_id": None, "ratio": 1.5, "item_id": uid},
            {"user_id": True, "ratio": 1.5, "item_id": uid},
            {"user_id": "abc", "ratio": 1.5, "item_id": uid},
            {"user_id": 1.5, "ratio": 1.5, "item_id": uid},
            {"user_id": 1, "ratio": True, "item_id": uid},
            {"user_id": 1, "ratio": float("nan"), "item_id": uid},
            {"user_id": 1, "ratio": float("inf"), "item_id": uid},
            {"user_id": 1, "ratio": "bad", "item_id": uid},
            {"user_id": 1, "ratio": 1.5, "item_id": "not-a-uuid"},
        ):
            with self.assertRaises(ValueError):
                app.url_for("typed_item", **bad_kwargs)

        # View.as_view validates name/methods/decorators; MethodView 405 Allow includes HEAD/OPTIONS
        class BadMethodsView(View):
            methods = "GET"  # type: ignore[assignment]

        class BadDecoratorsView(View):
            decorators = ("not-callable",)  # type: ignore[assignment]

        with self.assertRaises(ValueError):
            View.as_view(123)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            BadMethodsView.as_view("bad_methods")
        with self.assertRaises(TypeError):
            BadDecoratorsView.as_view("bad_decorators")

        class ReadOnlyItem(MethodView):
            def get(self):
                return "item"

        app.add_url_rule(
            "/ro",
            endpoint="ro",
            view_func=ReadOnlyItem.as_view("ro"),
            methods=("GET", "POST"),
        )
        disallowed = app.test_client().post("/ro")
        self.assertEqual(disallowed.status_code, 405)
        self.assertEqual(disallowed.headers["allow"], "GET, HEAD, OPTIONS")

        # Security / Redis / Frontend / Config / Request / Supervisor validation
        secret = "s" * 32
        with self.assertRaises(TypeError):
            TokenSigner(secret, salt=123)  # type: ignore[arg-type]
        signer = TokenSigner(secret)
        token = signer.dumps({"ok": True})
        with self.assertRaises(ValueError):
            signer.loads(token, max_age="10")  # type: ignore[arg-type]

        with self.assertRaises(ValueError):
            RateLimitMiddleware(limit=10, window="60")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            RateLimitMiddleware(limit=10, key="ip")  # type: ignore[arg-type]

        csrf_app = Higuma(__name__, static_folder=None)
        csrf_app.add_middleware(SessionMiddleware, secret)

        @csrf_app.get("/bad-csrf")
        def bad_csrf():
            return csrf_token(123)  # type: ignore[arg-type]

        with self.assertRaises(ValueError), csrf_app.test_request_context("/") as req:
            req.session = {}
            csrf_token(123)  # type: ignore[arg-type]

        with self.assertRaises(TypeError):
            RedisSessionMiddleware(secret, store=None)  # type: ignore[arg-type]

        class StringStore(RedisSessionStore):
            def __init__(self, payload: str, max_bytes: int) -> None:
                super().__init__(object(), prefix="app:", ttl=60, max_bytes=max_bytes)
                self._payload = payload

            def load(self, sid: str):
                return self._payload

        big_utf8 = json.dumps(
            {"data": {"text": "熊" * 20}, "permanent": False},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self.assertLessEqual(len(big_utf8), 64)
        self.assertGreater(len(big_utf8.encode("utf-8")), 64)
        redis_mw = RedisSessionMiddleware(secret, store=StringStore(big_utf8, max_bytes=64))
        sid = "a" * 43
        signed_cookie = f"{sid}.{redis_mw._signature(sid)}"
        self.assertEqual(redis_mw._load(signed_cookie), {})

        with self.assertRaises(ValueError):
            RedisRateLimitMiddleware(object(), prefix="app:", limit=5, window="60")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            RedisRateLimitMiddleware(object(), prefix="app:", limit=5, key="ip")  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text(json.dumps({"main.js": {"file": "assets/main.js"}}))
            manifest = ViteManifest(manifest_path)
            with self.assertRaises(TypeError):
                manifest.tags("main.js", nonce=123)  # type: ignore[arg-type]
            with self.assertRaises(ValueError):
                manifest.tags("main.js", nonce="bad\nnonce")

        with self.assertRaises(TypeError):
            Config().from_prefixed_env(loads="not-callable")  # type: ignore[arg-type]

        bad_charset_req = Request(
            {
                "method": "POST",
                "path": "/",
                "headers": {"content-type": "text/plain; charset=utf-8\x00"},
                "body": b"hello",
            }
        )
        with self.assertRaises(BadRequest):
            _ = bad_charset_req.text

        with self.assertRaises(ValueError):
            app.run(host="   ")
        with self.assertRaises(ValueError):
            Supervisor("example:app", host="")
        with self.assertRaises(ValueError):
            Supervisor("example:app", startup_timeout="15")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            Supervisor("example:app", restart_window="60")  # type: ignore[arg-type]

    def test_template_dataclass_context_response_mounts_body_cli_and_signal(self) -> None:
        import io
        import tempfile
        from dataclasses import dataclass
        from decimal import Decimal
        from enum import Enum
        from http import HTTPStatus
        from pathlib import Path

        from higuma import BadRequest, Response, Signal, make_response, render_template
        from higuma.body import BodyBuffer
        from higuma.mounts import asgi_view, wsgi_view

        class Badge(Enum):
            GOLD = "gold"

        @dataclass
        class BearCard:
            name: str
            badge: Badge
            score: Decimal

        with tempfile.TemporaryDirectory() as tmp:
            templates = Path(tmp) / "templates"
            templates.mkdir()
            (templates / "card.html").write_text(
                "{{ card.name }}|{{ card.badge }}|{{ card.score }}", encoding="utf-8"
            )
            tpl_app = Higuma(__name__, template_folder=str(templates), static_folder=None)

            @tpl_app.get("/card")
            def card_view():
                return render_template(
                    "card.html",
                    card=BearCard(name="Higuma", badge=Badge.GOLD, score=Decimal("9.5")),
                )

            rendered = tpl_app.test_client().get("/card")
            self.assertEqual(rendered.status_code, 200)
            self.assertEqual(rendered.text, "Higuma|gold|9.5")

        # Response / make_response strict integer status + HTTPStatus + cookie arg validation
        self.assertEqual(Response("ok", HTTPStatus.CREATED).status_code, 201)
        for bad_status in (True, False, 200.5, "200"):
            with self.assertRaises(ValueError):
                Response("ok", bad_status)  # type: ignore[arg-type]
            with self.assertRaises(ValueError):
                make_response(("ok", bad_status))  # type: ignore[arg-type]
            with self.assertRaises(ValueError):
                make_response(("ok", bad_status, {"x-a": "1"}))  # type: ignore[arg-type]

        resp = Response("ok")
        with self.assertRaises(ValueError):
            resp.set_cookie("sid", "1", samesite=123)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            resp.set_cookie("sid", "1", path=123)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            resp.set_cookie("sid", "1", domain=123)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            resp.set_cookie("sid", "1", expires=123)  # type: ignore[arg-type]

        # Mounted WSGI / ASGI validation and protocol safety
        with self.assertRaises(TypeError):
            wsgi_view("not-callable", "/sub")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            asgi_view("not-callable", "/sub")  # type: ignore[arg-type]

        mount_app = Higuma(__name__, static_folder=None)

        def early_body_wsgi(environ, start_response):
            yield b"before-start"
            start_response("200 OK", [])

        async def early_body_asgi(scope, receive, send):
            await send({"type": "http.response.body", "body": b"oops"})

        async def bytearray_asgi(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": bytearray(b"hi"), "more_body": True})
            await send({"type": "http.response.body", "body": None, "more_body": False})

        mount_app.mount_wsgi("/early-wsgi", early_body_wsgi, name="early_wsgi")
        mount_app.mount_asgi("/early-asgi", early_body_asgi, name="early_asgi")
        mount_app.mount_asgi("/ba-asgi", bytearray_asgi, name="ba_asgi")

        mount_client = mount_app.test_client()
        self.assertEqual(mount_client.get("/early-wsgi/").status_code, 500)
        self.assertEqual(mount_client.get("/early-asgi/").status_code, 500)
        ok_asgi = mount_client.get("/ba-asgi/")
        self.assertEqual(ok_asgi.status_code, 200)
        self.assertEqual(ok_asgi.body, b"hi")

        # BodyBuffer.fill_from rejects negative / bool / non-int Content-Length
        for bad_length in (-1, True, "5"):
            buf = BodyBuffer(64)
            with self.assertRaises(BadRequest):
                buf.fill_from(io.BytesIO(b"hello"), bad_length, False)  # type: ignore[arg-type]
            self.assertTrue(buf.closed)

        # CommandGroup.command and CliRunner validation
        cli_app = Higuma(__name__, static_folder=None)
        with self.assertRaises(ValueError):
            cli_app.cli.command("")(lambda: None)
        with self.assertRaises(ValueError):
            cli_app.cli.command(123)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            cli_app.cli.command("ok", help=123)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            cli_app.cli.command("ok")("not-callable")  # type: ignore[arg-type]
        bytes_result = cli_app.test_cli_runner().invoke(b"routes")  # type: ignore[arg-type]
        self.assertEqual(bytes_result.exit_code, 1)
        self.assertIsInstance(bytes_result.exception, TypeError)

        # Signal name validation
        with self.assertRaises(ValueError):
            Signal("")
        with self.assertRaises(ValueError):
            Signal(123)  # type: ignore[arg-type]

    def test_decimal_params_openapi_auth_observability_and_orm_tablename(self) -> None:
        from dataclasses import dataclass
        from decimal import Decimal

        from higuma import (
            AnonymousUser,
            AuthManager,
            Body,
            HealthChecks,
            Model,
            Observability,
            QueryParam,
            String,
            current_user,
        )
        from higuma.parameters import _convert_value

        @dataclass
        class PriceInput:
            amount: Decimal

        app = Higuma(__name__, static_folder=None)

        @app.post(
            "/price",
            responses={422: {"description": "Custom validation failure"}},
        )
        def calc_price(
            fee: Annotated[Decimal, QueryParam(ge=0)],
            payload: Annotated[PriceInput, Body()],
        ):
            return {"total": payload.amount + fee}

        @app.post("/reset", status_code=205)
        def reset_form():
            return None

        client = app.test_client()
        ok = client.post("/price?fee=1.25", json={"amount": "10.50"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json, {"total": "11.75"})

        # Reject bool, NaN, Infinity, non-numeric, and out-of-range Decimal inputs
        for bad_fee in ("NaN", "Infinity", "abc", "-1"):
            self.assertEqual(
                client.post(f"/price?fee={bad_fee}", json={"amount": "1.0"}).status_code, 422
            )
        self.assertEqual(client.post("/price?fee=1", json={"amount": True}).status_code, 422)
        self.assertEqual(_convert_value([], tuple[()]), ())

        # OpenAPI: Decimal schema, 205 omits content, int response keys normalized without duplicate 422
        spec = app.openapi()
        price_op = spec["paths"]["/price"]["post"]
        self.assertEqual(
            price_op["parameters"][0]["schema"],
            {"type": "string", "format": "decimal", "minimum": 0},
        )
        self.assertEqual(
            spec["components"]["schemas"]["PriceInput"]["properties"]["amount"],
            {"type": "string", "format": "decimal"},
        )
        self.assertEqual(
            price_op["responses"]["422"],
            {"description": "Custom validation failure"},
        )
        self.assertNotIn(422, price_op["responses"])
        self.assertNotIn("content", spec["paths"]["/reset"]["post"]["responses"]["205"])

        # AnonymousUser equality/hash and AuthManager user_loader callability validation
        self.assertEqual(AnonymousUser(), AnonymousUser())
        self.assertEqual(hash(AnonymousUser()), hash(AnonymousUser()))
        with app.test_request_context("/"):
            self.assertEqual(current_user, AnonymousUser())
            self.assertEqual(hash(current_user), hash(AnonymousUser()))
        with self.assertRaises(TypeError):
            AuthManager(user_loader="not-callable")  # type: ignore[arg-type]
        auth = AuthManager()
        with self.assertRaises(TypeError):
            auth.load_user("not-callable")  # type: ignore[arg-type]

        # HealthChecks non-numeric timeout + Observability.wrap_asgi memoryview headers & None body
        with self.assertRaises(ValueError):
            HealthChecks(timeout="1.0")  # type: ignore[arg-type]

        obs = Observability()
        wrapped_app = Higuma(__name__, static_folder=None)

        async def custom_asgi(scope, receive, send):
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(memoryview(b"x-request-id"), b"old"), (b"content-length", b"0")],
                }
            )
            await send({"type": "http.response.body", "body": None, "more_body": False})

        wrapped_app._asgi_application = custom_asgi
        observed = obs.wrap_asgi(wrapped_app)

        async def run_observed():
            sent_events = []

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(event):
                sent_events.append(event)

            await observed(
                {"type": "http", "method": "GET", "path": "/", "headers": []},
                receive,
                send,
            )
            return sent_events

        events = asyncio.run(run_observed())
        start_headers = [bytes(k).lower() for k, _ in events[0]["headers"]]
        self.assertEqual(start_headers.count(b"x-request-id"), 1)

        # ORM explicit __tablename__ validated at class definition time
        with self.assertRaises(ValueError):

            class BadTable(Model):
                __tablename__ = "users; DROP TABLE users"
                name = String()

    def test_app_and_blueprint_init_endpoint_hook_and_mount_validation(self) -> None:
        import tempfile

        from higuma import Blueprint

        with self.assertRaises(ValueError):
            Higuma("")
        with self.assertRaises(ValueError):
            Higuma(123)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            Higuma(__name__, lifespan="not-callable")  # type: ignore[arg-type]

        app = Higuma(__name__, static_folder=None)
        bp = Blueprint("api", __name__)

        # Route / WebSocket endpoint and callable validation
        with self.assertRaises(TypeError):
            app.add_url_rule("/bad", endpoint="bad", view_func="not-callable")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            app.add_url_rule("/bad", endpoint="", view_func=lambda: "ok")
        with self.assertRaises(ValueError):
            app.add_url_rule("/bad", endpoint=123, view_func=lambda: "ok")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            app.get("/bad", endpoint="")(lambda: "ok")
        with self.assertRaises(TypeError):
            app.get("/bad")("not-callable")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            app.websocket("/bad-ws", endpoint="")(lambda ws: None)
        with self.assertRaises(TypeError):
            app.websocket("/bad-ws")("not-callable")  # type: ignore[arg-type]

        with self.assertRaises(ValueError):
            bp.get("/bad", endpoint="")(lambda: "ok")
        with self.assertRaises(ValueError):
            bp.get("/bad", endpoint=123)(lambda: "ok")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            bp.get("/bad")("not-callable")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            bp.websocket("/bad-ws", endpoint="")(lambda ws: None)
        with self.assertRaises(TypeError):
            bp.websocket("/bad-ws")("not-callable")  # type: ignore[arg-type]

        # All app and blueprint hook decorators reject non-callables upfront
        for register in (
            app.before_request,
            app.after_request,
            app.teardown_request,
            app.teardown_appcontext,
            app.context_processor,
            app.on_startup,
            app.on_shutdown,
            app.url_value_preprocessor,
            app.url_defaults,
            bp.before_request,
            bp.after_request,
            bp.teardown_request,
            bp.context_processor,
            bp.url_value_preprocessor,
            bp.url_defaults,
            bp.before_app_request,
            bp.after_app_request,
            bp.teardown_app_request,
            bp.app_context_processor,
            bp.app_url_value_preprocessor,
            bp.app_url_defaults,
        ):
            with self.assertRaises(TypeError):
                register("not-callable")  # type: ignore[arg-type]

        # Mount names must be non-empty strings
        with self.assertRaises(ValueError):
            app.mount_wsgi("/sub", lambda env, sr: [b""], name="")
        with self.assertRaises(ValueError):
            app.mount_asgi("/sub", lambda s, r, sd: None, name=123)  # type: ignore[arg-type]
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            app.mount_static("/files", tmp, name="")

    def test_file_response_redirect_and_header_mapping_contracts(self) -> None:
        import tempfile

        from higuma import FileResponse, Headers, RedirectResponse, Response

        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(IsADirectoryError):
            FileResponse(tmp)

        with self.assertRaises(TypeError):
            RedirectResponse(123)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            RedirectResponse("   ")

        req_headers = Headers({"Content-Type": "text/plain"})
        self.assertFalse(123 in req_headers)
        self.assertIsNone(req_headers.get(123))  # type: ignore[arg-type]
        self.assertEqual(req_headers.get(123, "fallback"), "fallback")  # type: ignore[arg-type]
        with self.assertRaises(KeyError):
            _ = req_headers[123]  # type: ignore[index]

        resp = Response("ok", headers={"X-Test": "1"})
        self.assertFalse(123 in resp.headers)
        self.assertIsNone(resp.headers.get(123))  # type: ignore[arg-type]
        self.assertEqual(resp.headers.pop(123, "fallback"), "fallback")  # type: ignore[arg-type]
        with self.assertRaises(KeyError):
            _ = resp.headers[123]  # type: ignore[index]
        with self.assertRaises(KeyError):
            del resp.headers[123]  # type: ignore[arg-type]
        with self.assertRaises(KeyError):
            resp.headers.pop(123)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            resp.headers.pop("x-test", "a", "b")
        copied = resp.headers.copy()
        self.assertEqual(copied["X-TEST"], "1")

    def test_bytes_like_body_cookie_file_runtime_and_fast_path_cleanup(self) -> None:
        import asyncio
        import io
        import tempfile
        from pathlib import Path

        from higuma import (
            BackgroundTask,
            FileResponse,
            Higuma,
            Request,
            Response,
            StreamingResponse,
            make_response,
        )
        from higuma.body import BodyBuffer, receive_body
        from higuma.mounts import asgi_view, wsgi_view

        # Response / make_response / set_data must reject int/bool instead of allocating NUL bytes
        for bad_body in (5, True, [65, 66]):
            with self.assertRaises(TypeError):
                Response(bad_body)  # type: ignore[arg-type]
            with self.assertRaises(TypeError):
                Response().set_data(bad_body)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            make_response(42)  # type: ignore[arg-type]
        self.assertEqual(Response(bytearray(b"ok")).body, b"ok")
        self.assertEqual(Response(memoryview(b"mv")).body, b"mv")
        mutable = Response()
        mutable.set_data(bytearray(b"updated"))
        self.assertEqual(mutable.body, b"updated")

        # set_cookie / delete_cookie must require str key and value
        with self.assertRaises(TypeError):
            Response().set_cookie(123, "v")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            Response().set_cookie("k", 123)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            Response().delete_cookie(123)  # type: ignore[arg-type]

        # Request / receive_body / wsgi_view / asgi_view bytes-like guards
        self.assertEqual(Request({"body": None}).body, b"")
        self.assertEqual(Request({"body": bytearray(b"hi")}).body, b"hi")
        with self.assertRaises(TypeError):
            Request({"body": 5})

        async def check_receive_body() -> None:
            buf_none = BodyBuffer(64, 32)
            events_none = iter([{"type": "http.request", "body": None, "more_body": False}])

            async def recv_none() -> dict[str, Any]:
                return next(events_none)

            self.assertTrue(await receive_body(buf_none, recv_none))
            self.assertEqual(buf_none.size, 0)
            buf_none.close()

            buf_bad = BodyBuffer(64, 32)
            events_bad = iter([{"type": "http.request", "body": 5, "more_body": False}])

            async def recv_bad() -> dict[str, Any]:
                return next(events_bad)

            with self.assertRaises(TypeError):
                await receive_body(buf_bad, recv_bad)
            self.assertTrue(buf_bad.closed)

        asyncio.run(check_receive_body())

        def bad_wsgi(_env: Any, start_response: Any) -> list[Any]:
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [5]

        with self.assertRaises(TypeError):
            wsgi_view(bad_wsgi, "/sub")(Request({"path": "/sub", "method": "GET"}))

        async def bad_asgi(_scope: Any, _receive: Any, send: Any) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": 5, "more_body": False})

        with self.assertRaises(TypeError):
            asyncio.run(asgi_view(bad_asgi, "/sub")(Request({"path": "/sub", "method": "GET"})))

        # TestClient multipart file validation & ASGITestWebSocket payload validation
        app = Higuma(__name__, static_folder=None)

        @app.post("/upload")
        def upload() -> str:
            return "ok"

        @app.websocket("/ws")
        async def ws_echo(ws: Any) -> None:
            await ws.asend_text(await ws.areceive_text())

        with self.assertRaises(TypeError):
            app.test_client().post("/upload", files={"file": ("a.txt", 16)})

        async def check_asgi_ws_validation() -> None:
            async with (
                app.test_async_client() as client,
                client.websocket_connect("/ws") as ws,
            ):
                with self.assertRaises(TypeError):
                    await ws.send_text(123)  # type: ignore[arg-type]
                with self.assertRaises(TypeError):
                    await ws.send_bytes(5)  # type: ignore[arg-type]
                await ws.send_text("ok")
                self.assertEqual(await ws.receive_text(), "ok")

        asyncio.run(check_asgi_ws_validation())

        # FileResponse runtime size/type validation parity across WSGI, ASGI, and TestClient
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            shrinking = tmp_dir / "shrink.txt"
            late = tmp_dir / "late.txt"
            file_app = Higuma(__name__, static_folder=None)

            @file_app.get("/shrink")
            def shrink_route() -> FileResponse:
                shrinking.write_bytes(b"0123456789")
                response = FileResponse(shrinking)
                shrinking.write_bytes(b"01")
                return response

            @file_app.get("/late")
            def late_route() -> FileResponse:
                if late.exists():
                    late.unlink()
                response = FileResponse(late)
                late.write_bytes(b"appeared")
                return response

            # TestClient
            with self.assertRaises(OSError):
                file_app.test_client().get("/shrink")
            late_tc = file_app.test_client().get("/late")
            self.assertEqual(late_tc.body, b"appeared")
            self.assertEqual(late_tc.headers.get("content-length"), "8")

            # WSGI
            def run_wsgi(path: str) -> tuple[int, dict[str, str], bytes]:
                captured: dict[str, Any] = {}

                def start_response(status: str, headers: list[tuple[str, str]]) -> None:
                    captured["status"] = int(status.split(" ", 1)[0])
                    captured["headers"] = {k.lower(): v for k, v in headers}

                env = {
                    "REQUEST_METHOD": "GET",
                    "PATH_INFO": path,
                    "QUERY_STRING": "",
                    "SERVER_NAME": "localhost",
                    "SERVER_PORT": "80",
                    "wsgi.url_scheme": "http",
                    "wsgi.input": io.BytesIO(b""),
                }
                result = file_app.wsgi_app(env, start_response)
                try:
                    body = b"".join(result)
                finally:
                    result.close()
                return captured["status"], captured["headers"], body

            shrink_status, _, _ = run_wsgi("/shrink")
            self.assertEqual(shrink_status, 500)
            late_status, late_headers, late_body = run_wsgi("/late")
            self.assertEqual(late_status, 200)
            self.assertEqual(late_body, b"appeared")
            self.assertEqual(late_headers.get("content-length"), "8")

            # ASGI
            async def check_asgi_files() -> None:
                async with file_app.test_async_client() as client:
                    resp_shrink = await client.get("/shrink")
                    self.assertEqual(resp_shrink.status_code, 500)
                    resp_late = await client.get("/late")
                    self.assertEqual(resp_late.status_code, 200)
                    self.assertEqual(resp_late.body, b"appeared")
                    self.assertEqual(resp_late.headers.get("content-length"), "8")

            asyncio.run(check_asgi_files())

        # ASGI async fast-path must close stream and run background if handoff fails before respond()
        from higuma.async_dispatch import run as run_fast_path

        fast_app = Higuma(__name__, static_folder=None)
        cleaned: list[str] = []

        class TrackedStream:
            def __aiter__(self) -> TrackedStream:
                return self

            async def __anext__(self) -> bytes:
                raise StopAsyncIteration

            async def aclose(self) -> None:
                cleaned.append("closed")

        @fast_app.get("/fast-stream")
        async def fast_stream() -> StreamingResponse:
            return StreamingResponse(
                TrackedStream(),
                background=BackgroundTask(cleaned.append, "bg"),
            )

        async def check_fast_path_handoff_failure() -> None:
            route, params, allowed = fast_app._match_route("/fast-stream", "GET")
            assert route is not None
            raw = {
                "path": "/fast-stream",
                "method": "GET",
                "body": b"",
                "raw_headers": [(b"host", b"localhost")],
                "scheme": "http",
                "query_string": "",
                "client_addr": "127.0.0.1",
                "root_path": "",
                "path_params": params,
                "route_pattern": route.rule,
                "allowed_methods": allowed,
            }

            async def never_recv() -> dict[str, Any]:
                return await asyncio.Future()

            async def noop_send(_event: dict[str, Any]) -> None:
                return None

            async def failing_worker(_fn: Any, _recv: Any) -> None:
                raise RuntimeError("worker unavailable")

            with self.assertRaises(RuntimeError):
                await run_fast_path(
                    fast_app,
                    route,
                    raw,
                    noop_send,
                    never_recv,
                    lambda _resp: None,
                    failing_worker,
                )

        asyncio.run(check_fast_path_handoff_failure())
        self.assertEqual(cleaned, ["closed", "bg"])

    def test_canonical_base64url_multipart_headers_and_middleware_validation(self) -> None:
        from higuma import (
            CORSMiddleware,
            CSRFProtection,
            Higuma,
            PasswordHasher,
            SecurityHeadersMiddleware,
            Session,
            SessionMiddleware,
            TokenSigner,
            TrustedHostMiddleware,
            middleware,
        )

        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

        def flip_unused_bits(b64url_43: str) -> str:
            # 32-byte HMAC-SHA256 digest in 43 base64url chars has 2 unused LSBs in the last char.
            index = alphabet.index(b64url_43[-1])
            return b64url_43[:-1] + alphabet[index ^ 1]

        secret = "canonical-security-secret-with-at-least-32-bytes"

        # SessionMiddleware must reject non-base64 junk, '=' padding, and trailing-bit malleability
        sess_mw = SessionMiddleware(secret)
        raw_cookie = sess_mw._dump(Session({"user": "bear"}))
        self.assertEqual(sess_mw._load(raw_cookie)["user"], "bear")
        self.assertEqual(dict(sess_mw._load(raw_cookie + "!!!")), {})
        self.assertEqual(dict(sess_mw._load(raw_cookie + "=")), {})
        payload_part, sig_part = raw_cookie.split(".", 1)
        self.assertEqual(dict(sess_mw._load(f"{payload_part}.{flip_unused_bits(sig_part)}")), {})
        self.assertEqual(dict(sess_mw._load(f"extra.{raw_cookie}")), {})

        # TokenSigner must reject '=' padding, extra dots, and trailing-bit malleability
        signer = TokenSigner(secret)
        token = signer.dumps({"ok": True})
        self.assertEqual(signer.loads(token), {"ok": True})
        tok_payload, tok_sig = token.split(".", 1)
        for mutated in (
            token + "=",
            token + "!!!",
            f"{tok_payload}.{flip_unused_bits(tok_sig)}",
            f"extra.{token}",
        ):
            with self.assertRaises(ValueError):
                signer.loads(mutated)

        # PasswordHasher must reject non-canonical scrypt params and non-canonical base64url
        hasher = PasswordHasher(n=2**10)
        encoded = hasher.hash("secret-password")
        self.assertTrue(hasher.verify("secret-password", encoded))
        parts = encoded.split("$")
        for mutated_hash in (
            "$".join([parts[0], parts[1], f"+{parts[2]}", *parts[3:]]),
            "$".join([parts[0], parts[1], f"0{parts[2]}", *parts[3:]]),
            "$".join([parts[0], parts[1], "1_024", *parts[3:]]),
            "$".join([*parts[:-1], parts[-1] + "="]),
            "$".join([*parts[:-1], flip_unused_bits(parts[-1])]),
        ):
            self.assertFalse(hasher.verify("secret-password", mutated_hash))
            self.assertTrue(hasher.needs_rehash(mutated_hash))

        # CSRFProtection safe_methods item validation
        with self.assertRaises(TypeError):
            CSRFProtection(safe_methods=(123,))  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            CSRFProtection(safe_methods=(" ",))

        # Multipart duplicate Content-Transfer-Encoding and duplicate Content-Type rejection
        mp_app = Higuma(__name__, static_folder=None)

        @mp_app.post("/mp")
        def mp(req: Any) -> dict[str, Any]:
            return {"form": req.form.to_dict()}

        dup_cte = (
            b"--bnd\r\n"
            b'Content-Disposition: form-data; name="a"\r\n'
            b"Content-Transfer-Encoding: binary\r\n"
            b"Content-Transfer-Encoding: base64\r\n\r\n"
            b"YWJj\r\n"
            b"--bnd--\r\n"
        )
        self.assertEqual(
            mp_app.test_client()
            .post(
                "/mp",
                data=dup_cte,
                content_type="multipart/form-data; boundary=bnd",
            )
            .status_code,
            400,
        )
        dup_ct = (
            b"--bnd\r\n"
            b'Content-Disposition: form-data; name="a"\r\n'
            b"Content-Type: text/plain\r\n"
            b"Content-Type: text/html\r\n\r\n"
            b"hello\r\n"
            b"--bnd--\r\n"
        )
        self.assertEqual(
            mp_app.test_client()
            .post(
                "/mp",
                data=dup_ct,
                content_type="multipart/form-data; boundary=bnd",
            )
            .status_code,
            400,
        )

        # Middleware constructor & decorator validation
        with self.assertRaises(TypeError):
            CORSMiddleware(allow_origins=[123])  # type: ignore[list-item]
        with self.assertRaises(ValueError):
            CORSMiddleware(allow_origins=["https://example.com\r\nX-Bad: 1"])
        with self.assertRaises(ValueError):
            CORSMiddleware(allow_methods=[" "])
        with self.assertRaises(TypeError):
            SecurityHeadersMiddleware(content_security_policy=123)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            SecurityHeadersMiddleware(frame_options="DENY\r\nEvil: 1")
        with self.assertRaises(TypeError):
            TrustedHostMiddleware([123])  # type: ignore[list-item]
        with self.assertRaises(ValueError):
            TrustedHostMiddleware([" "])
        with self.assertRaises(TypeError):
            middleware("not-callable")  # type: ignore[arg-type]

    def test_routing_url_for_exceptions_config_and_vite_validation(self) -> None:
        import json
        import tempfile
        from http import HTTPStatus
        from pathlib import Path

        from higuma import Config, Higuma, HTTPException, ViteManifest, abort

        app = Higuma(__name__, static_folder=None)

        with self.assertRaises(TypeError):
            app.get(123)(lambda: "ok")  # type: ignore[arg-type]

        @app.get("/items/<slug>/detail")
        def detail(slug: str) -> str:
            return slug

        @app.get("/files/<path:filepath>")
        def files(filepath: str) -> str:
            return filepath

        with self.assertRaises(TypeError):
            app.url_for(123)  # type: ignore[arg-type]
        for bad_slug in ("", "bad\nline", "bad\x00nul"):
            with self.assertRaises(ValueError):
                app.url_for("detail", slug=bad_slug)
        with self.assertRaises(ValueError):
            app.url_for("files", filepath="")
        self.assertEqual(app.url_for("detail", slug="bear"), "/items/bear/detail")
        self.assertEqual(app.url_for("files", filepath="docs/a.txt"), "/files/docs/a.txt")

        # HTTPException / abort validation
        exc = HTTPException(HTTPStatus.NOT_FOUND, headers={"X-Err": "1"})
        self.assertIs(type(exc.status_code), int)
        self.assertEqual(exc.status_code, 404)
        self.assertEqual(exc.headers, {"X-Err": "1"})
        with self.assertRaises(TypeError):
            HTTPException(400, headers="bad")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            HTTPException(400, headers={"Bad Header": "1"})
        with self.assertRaises(ValueError):
            HTTPException(400, headers={"X-Test": "a\r\nb"})
        with self.assertRaises(ValueError):
            abort([404])  # type: ignore[arg-type]
        with self.assertRaises(HTTPException) as caught:
            abort(HTTPStatus.FORBIDDEN)
        self.assertIs(type(caught.exception.status_code), int)
        self.assertEqual(caught.exception.status_code, 403)

        # Config.from_file(load=...) and Config.get_namespace()
        cfg = Config()
        with self.assertRaises(TypeError):
            cfg.from_file("missing.json", load=None, silent=True)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            cfg.get_namespace(123)  # type: ignore[arg-type]
        cfg[123] = "ignored"  # type: ignore[index]
        cfg["APP_HOST"] = "localhost"
        self.assertEqual(cfg.get_namespace("APP_"), {"host": "localhost"})

        # ViteManifest port error message and non-empty nonce validation
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text(
                json.dumps({"main.js": {"file": "assets/main.js"}}),
                encoding="utf-8",
            )
            for bad_base in (
                "https://cdn.example.com:99999/assets/",
                "https://cdn.example.com:abc/assets/",
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "base_url must be a root-relative path or absolute HTTP\\(S\\) URL",
                ):
                    ViteManifest(manifest_path, base_url=bad_base)
            manifest = ViteManifest(manifest_path)
            for bad_nonce in ("", "   "):
                with self.assertRaises(ValueError):
                    manifest.tags("main.js", nonce=bad_nonce)


if __name__ == "__main__":
    unittest.main()
