from __future__ import annotations

import asyncio
import sys
import unittest
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Annotated, Optional
from uuid import UUID

from higuma import (
    BackgroundTasks,
    Body,
    Cookie,
    Depends,
    EventSourceResponse,
    File,
    Form,
    Header,
    Higuma,
    MethodView,
    PathParam,
    QueryParam,
    ServerSentEvent,
    StreamingResponse,
    UploadFile,
    redirect,
    request,
)


class Visibility(Enum):
    PUBLIC = "public"
    PRIVATE = "private"


@dataclass
class ItemInput:
    name: str
    quantity: int
    available_on: date


@dataclass
class ItemOutput:
    identifier: UUID
    name: str
    visibility: Visibility


class ModernApiTests(unittest.TestCase):
    def make_app(self) -> Higuma:
        return Higuma(__name__, static_folder=None, openapi_url=None, docs_url=None)

    def test_annotated_inputs_nested_dependency_cleanup_and_dataclass_response(self) -> None:
        app = self.make_app()
        events: list[str] = []

        def common_query(
            search: Annotated[str, QueryParam(min_length=2, max_length=12)] = "all",
        ):
            events.append(f"open:{search}")
            yield search
            events.append(f"close:{search}")

        @app.post("/items/<uuid:identifier>")
        def create_item(
            identifier: Annotated[UUID, PathParam()],
            payload: Annotated[ItemInput, Body()],
            search: Annotated[str, Depends(common_query)],
            request_id: Annotated[str, Header("x-request-id")],
            session_id: Annotated[str | None, Cookie()] = None,
        ) -> ItemOutput:
            self.assertEqual(search, "bears")
            self.assertEqual(request_id, "req-1")
            self.assertEqual(session_id, "session-1")
            self.assertEqual(payload.quantity, 3)
            return ItemOutput(identifier, payload.name, Visibility.PUBLIC)

        client = app.test_client()
        client.cookies["session_id"] = "session-1"
        identifier = "12345678-1234-5678-1234-567812345678"
        response = client.post(
            f"/items/{identifier}?search=bears",
            headers={"x-request-id": "req-1"},
            json={"name": "Higuma", "quantity": 3, "available_on": "2026-08-27"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json,
            {"identifier": identifier, "name": "Higuma", "visibility": "public"},
        )
        self.assertEqual(events, ["open:bears", "close:bears"])

    def test_validation_errors_are_structured_and_safe(self) -> None:
        app = self.make_app()

        @app.get("/search")
        def search(page: Annotated[int, QueryParam(ge=1, le=10)]) -> dict[str, int]:
            return {"page": page}

        missing = app.test_client().get("/search")
        self.assertEqual(missing.status_code, 422)
        self.assertEqual(missing.json["detail"][0]["loc"], ["query", "page"])

        invalid = app.test_client().get("/search?page=100")
        self.assertEqual(invalid.status_code, 422)
        self.assertNotIn("traceback", invalid.text.lower())

        @dataclass
        class Login:
            username: str
            password: int

        @app.post("/login")
        def login(payload: Annotated[Login, Body()]):
            return {"ok": True}

        @app.post("/optional-login")
        def optional_login(payload: Annotated[Login | None, Body()] = None):
            return {"provided": payload is not None}

        sensitive = app.test_client().post(
            "/login",
            json={"username": "bear", "password": "not-an-int"},
        )
        self.assertEqual(sensitive.status_code, 422)
        self.assertNotIn("not-an-int", sensitive.text)
        self.assertIn('"password":"***"', sensitive.text)

        malformed = app.test_client().post(
            "/login",
            data=b'{"password":"must-not-leak"',
            headers={"content-type": "application/json"},
        )
        self.assertEqual(malformed.status_code, 422)
        self.assertNotIn(b"must-not-leak", malformed.body)
        self.assertNotIn("input", malformed.json["detail"][0])

        wrong_media_type = app.test_client().post(
            "/login",
            data=b'{"password":"must-not-leak"}',
            headers={"content-type": "text/plain"},
        )
        self.assertEqual(wrong_media_type.status_code, 422)
        self.assertNotIn(b"must-not-leak", wrong_media_type.body)
        self.assertEqual(
            app.test_client().post("/optional-login").json,
            {"provided": False},
        )

        @app.get("/multiple")
        def multiple(values: Annotated[list[int] | None, QueryParam()] = None):
            return {"values": values}

        self.assertEqual(
            app.test_client().get("/multiple?values=1&values=2").json,
            {"values": [1, 2]},
        )

        @app.get("/nested-optional")
        def nested_optional(
            values: Optional[Annotated[list[int], QueryParam()]] = None,  # noqa: UP045
        ):
            return {"values": values}

        self.assertEqual(
            app.test_client().get("/nested-optional?values=3&values=4").json,
            {"values": [3, 4]},
        )

    def test_form_file_and_dependency_override(self) -> None:
        app = self.make_app()

        def actor() -> str:
            return "production"

        @app.post("/upload")
        def upload(
            title: Annotated[str, Form(min_length=2)],
            attachment: Annotated[UploadFile, File("asset")],
            current_actor: Annotated[str, Depends(actor)],
        ) -> dict[str, object]:
            return {
                "title": title,
                "filename": attachment.filename,
                "size": attachment.size,
                "actor": current_actor,
            }

        app.dependency_overrides[actor] = lambda: "test"
        response = app.test_client().post(
            "/upload",
            data={"title": "Bear"},
            files={"asset": ("bear.txt", b"higuma", "text/plain")},
        )
        self.assertEqual(
            response.json,
            {"title": "Bear", "filename": "bear.txt", "size": 6, "actor": "test"},
        )

    def test_streaming_sse_and_background_tasks(self) -> None:
        app = self.make_app()
        events: list[str] = []

        def stream_resource():
            events.append("dependency:open")
            yield "resource"
            events.append(f"dependency:close:{request.path}")

        @app.get("/stream")
        def stream(
            resource: Annotated[str, Depends(stream_resource)],
            background_tasks: BackgroundTasks,
        ) -> StreamingResponse:
            def chunks():
                events.append(f"chunk:{request.path}:{resource}")
                yield "one"
                events.append("chunk:two")
                yield b"two"

            background_tasks.add_task(events.append, "background")
            return StreamingResponse(chunks(), media_type="text/plain; charset=utf-8")

        @app.get("/async-stream")
        def async_stream() -> StreamingResponse:
            async def chunks():
                yield "a"
                await asyncio.sleep(0)
                yield "b"

            return StreamingResponse(chunks(), media_type="text/plain; charset=utf-8")

        @app.get("/events")
        def sse() -> EventSourceResponse:
            return EventSourceResponse(
                [
                    ServerSentEvent("first\nsecond", event="message", id="7", retry=1000),
                    {"status": "ok"},
                ]
            )

        stream_response = app.test_client().get("/stream")
        self.assertEqual(stream_response.body, b"onetwo")
        self.assertEqual(
            events,
            [
                "dependency:open",
                "chunk:/stream:resource",
                "chunk:two",
                "background",
                "dependency:close:/stream",
            ],
        )
        self.assertEqual(app.test_client().get("/async-stream").body, b"ab")

        event_response = app.test_client().get("/events")
        self.assertEqual(event_response.media_type, "text/event-stream; charset=utf-8")
        self.assertEqual(event_response.headers["cache-control"], "no-cache")
        self.assertIn(b"event: message\nid: 7\nretry: 1000\n", event_response.body)
        self.assertIn(b"data: first\ndata: second\n\n", event_response.body)
        self.assertIn(b'data: {"status":"ok"}\n\n', event_response.body)

    def test_openapi_includes_annotated_inputs_and_dependency_parameters(self) -> None:
        app = self.make_app()

        def pagination(limit: Annotated[int, QueryParam(ge=1, le=100)] = 20) -> int:
            return limit

        @app.post("/items/<uuid:identifier>")
        def create(
            identifier: Annotated[UUID, PathParam(description="Item ID")],
            payload: Annotated[ItemInput, Body(description="New item")],
            limit: Annotated[int, Depends(pagination)],
            trace: Annotated[str, Header("x-trace-id")],
        ) -> ItemOutput:
            raise NotImplementedError

        operation = app.openapi()["paths"]["/items/{identifier}"]["post"]
        parameters = {(item["in"], item["name"]): item for item in operation["parameters"]}
        self.assertEqual(parameters[("query", "limit")]["schema"]["maximum"], 100)
        self.assertEqual(parameters[("query", "limit")]["schema"]["default"], 20)
        self.assertTrue(parameters[("header", "x-trace-id")]["required"])
        self.assertIn("application/json", operation["requestBody"]["content"])
        self.assertIn("422", operation["responses"])

    def test_async_lifespan_and_legacy_lifecycle_hooks_run_in_test_context(self) -> None:
        events: list[str] = []

        @asynccontextmanager
        async def lifespan(app: Higuma):
            events.append("lifespan:start")
            yield {"ready": True}
            await asyncio.sleep(0)
            events.append("lifespan:stop")

        app = Higuma(
            __name__,
            static_folder=None,
            openapi_url=None,
            docs_url=None,
            lifespan=lifespan,
        )

        @app.on_startup
        def startup(current_app: Higuma) -> None:
            self.assertIs(current_app, app)
            events.append("hook:start")

        @app.on_shutdown
        async def shutdown() -> None:
            await asyncio.sleep(0)
            events.append("hook:stop")

        @app.get("/state")
        def state() -> dict[str, bool]:
            return {"ready": app.state["ready"]}

        with app.test_client() as client:
            self.assertEqual(client.get("/state").json, {"ready": True})
            self.assertEqual(events, ["lifespan:start", "hook:start"])

        self.assertEqual(
            events,
            ["lifespan:start", "hook:start", "hook:stop", "lifespan:stop"],
        )

    def test_response_model_filters_output_and_sets_declared_status(self) -> None:
        app = self.make_app()
        identifier = UUID("12345678-1234-5678-1234-567812345678")

        @app.post(
            "/modeled",
            response_model=ItemOutput,
            status_code=201,
        )
        def modeled() -> dict[str, object]:
            return {
                "identifier": str(identifier),
                "name": "Higuma",
                "visibility": "private",
                "internal_secret": "filtered",
            }

        @app.post("/modeled-tuple", response_model=ItemOutput, status_code=202)
        def modeled_tuple():
            return (
                {
                    "identifier": str(identifier),
                    "name": "Tuple",
                    "visibility": "public",
                    "internal_secret": "filtered",
                },
                202,
                {"x-result-form": "tuple"},
            )

        @app.post("/invalid-modeled", response_model=ItemOutput)
        def invalid_modeled():
            return {
                "identifier": "invalid-uuid",
                "name": "must-not-leak",
                "visibility": "private",
            }

        response = app.test_client().post("/modeled")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.json,
            {
                "identifier": str(identifier),
                "name": "Higuma",
                "visibility": "private",
            },
        )
        operation = app.openapi()["paths"]["/modeled"]["post"]
        self.assertIn("201", operation["responses"])
        schema = operation["responses"]["201"]["content"]["application/json"]["schema"]
        self.assertEqual(schema, {"$ref": "#/components/schemas/ItemOutput"})

        tuple_response = app.test_client().post("/modeled-tuple")
        self.assertEqual(tuple_response.status_code, 202)
        self.assertEqual(tuple_response.headers["x-result-form"], "tuple")
        self.assertEqual(
            tuple_response.json,
            {
                "identifier": str(identifier),
                "name": "Tuple",
                "visibility": "public",
            },
        )

        app.logger.disabled = True
        invalid_response = app.test_client().post("/invalid-modeled")
        self.assertEqual(invalid_response.status_code, 500)
        self.assertNotIn("must-not-leak", invalid_response.text)
        self.assertNotIn("invalid-uuid", invalid_response.text)

    def test_request_urls_and_modern_cookie_attributes(self) -> None:
        app = self.make_app()

        @app.get("/request-url")
        def request_url():
            response = app.jsonify(
                host=request.host,
                base_url=request.base_url,
                url=request.url,
            )
            if sys.version_info >= (3, 14):
                response.set_cookie("cross-site", "value", secure=True, partitioned=True)
            return response

        response = app.test_client().get(
            "/request-url?q=bear",
            headers={"host": "example.test"},
        )
        self.assertEqual(
            response.json,
            {
                "host": "example.test",
                "base_url": "http://example.test/",
                "url": "http://example.test/request-url?q=bear",
            },
        )
        if sys.version_info >= (3, 14):
            cookie = next(
                value
                for name, value in response.header_items
                if name == "set-cookie" and value.startswith("cross-site=")
            )
            self.assertIn("Partitioned", cookie)
            self.assertIn("Secure", cookie)

        with self.assertRaises(ValueError):
            app.jsonify(ok=True).set_cookie("bad", "value", samesite="invalid")
        with self.assertRaises(ValueError):
            app.jsonify(ok=True).set_cookie("bad", "value", partitioned=True)

    def test_test_client_follows_redirects_with_http_method_semantics(self) -> None:
        app = self.make_app()

        @app.post("/start")
        def start():
            return redirect("/result", 303)

        @app.route("/result", methods=("GET", "POST"))
        def result():
            return {"method": request.method, "body": request.text}

        followed = app.test_client().post(
            "/start",
            data="payload",
            follow_redirects=True,
        )
        self.assertEqual(followed.json, {"method": "GET", "body": ""})
        self.assertEqual([item.status_code for item in followed.history], [303])

        @app.post("/preserve")
        def preserve():
            return redirect("/result", 307)

        preserved = app.test_client().post(
            "/preserve",
            data="payload",
            follow_redirects=True,
        )
        self.assertEqual(preserved.json, {"method": "POST", "body": "payload"})

    def test_method_view_derives_methods_and_receives_path_parameters(self) -> None:
        app = self.make_app()

        class ItemView(MethodView):
            def get(self, item_id: int):
                return {"method": "GET", "item_id": item_id}

            def post(self, item_id: int):
                return {"method": "POST", "item_id": item_id}

        app.add_url_rule(
            "/class-items/<int:item_id>",
            endpoint="class_item",
            view_func=ItemView.as_view("class_item"),
        )

        client = app.test_client()
        self.assertEqual(
            client.get("/class-items/7").json,
            {"method": "GET", "item_id": 7},
        )
        self.assertEqual(
            client.post("/class-items/7").json,
            {"method": "POST", "item_id": 7},
        )
        self.assertEqual(client.delete("/class-items/7").status_code, 405)


if __name__ == "__main__":
    unittest.main()
