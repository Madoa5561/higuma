from __future__ import annotations

import asyncio
import unittest
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Annotated, TypedDict
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


if __name__ == "__main__":
    unittest.main()
