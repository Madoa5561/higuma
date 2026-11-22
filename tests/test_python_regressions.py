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


if __name__ == "__main__":
    unittest.main()
