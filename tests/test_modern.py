from __future__ import annotations

import asyncio
import sys
import unittest
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Annotated, Literal, Optional
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

    def test_method_view_supports_typed_parameters_dependencies_and_openapi(self) -> None:
        app = self.make_app()
        events: list[str] = []

        def dep() -> str:
            return "injected"

        class TypedItemView(MethodView):
            def get(
                self,
                item_id: int,
                q: Annotated[str, QueryParam(min_length=2)] = "all",
                source: Annotated[str, Depends(dep)] = "",
            ) -> dict[str, object]:
                return {"item_id": item_id, "q": q, "source": source}

            def post(
                self,
                item_id: int,
                payload: Annotated[ItemInput, Body()],
                tasks: BackgroundTasks,
            ) -> ItemOutput:
                tasks.add_task(events.append, f"created:{item_id}:{payload.name}")
                return ItemOutput(
                    identifier=UUID("12345678-1234-5678-1234-567812345678"),
                    name=payload.name,
                    visibility=Visibility.PUBLIC,
                )

        app.add_url_rule(
            "/typed-view/<int:item_id>",
            endpoint="typed_view",
            view_func=TypedItemView.as_view("typed_view"),
        )
        client = app.test_client()
        self.assertEqual(
            client.get("/typed-view/5?q=bear").json,
            {"item_id": 5, "q": "bear", "source": "injected"},
        )
        self.assertEqual(client.get("/typed-view/5?q=a").status_code, 422)
        created = client.post(
            "/typed-view/5",
            json={"name": "Bear", "quantity": 2, "available_on": "2026-11-22"},
        )
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json["name"], "Bear")
        self.assertEqual(events, ["created:5:Bear"])

        spec = app.openapi()["paths"]["/typed-view/{item_id}"]
        get_params = {(p["in"], p["name"]): p for p in spec["get"]["parameters"]}
        self.assertEqual(get_params[("query", "q")]["schema"]["minLength"], 2)
        self.assertTrue(spec["post"]["requestBody"]["required"])

    def test_openapi_dataclass_init_false_annotated_fields_and_optional_raw_body(self) -> None:
        from dataclasses import field

        @dataclass
        class ConstrainedModel:
            code: Annotated[str, QueryParam(min_length=3, max_length=8)]
            tier: Literal[1, 2] = 1
            computed: str = field(init=False, default="internal")

        app = self.make_app()

        @app.post("/constrained")
        def constrained(
            model: ConstrainedModel = Body(),  # noqa: B008 - verify default marker OpenAPI required
        ) -> dict[str, object]:
            return {"code": model.code, "tier": model.tier}

        @app.post("/only-text")
        def only_text(
            text: Annotated[str | None, Body()] = None,
        ) -> dict[str, object]:
            return {"text": text}

        @app.post("/only-bytes")
        def only_bytes(
            raw: Annotated[bytes | None, Body()] = None,
        ) -> dict[str, object]:
            return {"size": None if raw is None else len(raw)}

        schema = app.openapi()["components"]["schemas"]["ConstrainedModel"]
        self.assertNotIn("computed", schema["properties"])
        self.assertEqual(schema["properties"]["code"]["minLength"], 3)
        self.assertEqual(schema["properties"]["tier"]["type"], "integer")

        client = app.test_client()
        self.assertEqual(client.post("/only-text").json, {"text": None})
        self.assertEqual(
            client.post("/only-text", data="bear", headers={"content-type": "text/plain"}).json,
            {"text": "bear"},
        )
        self.assertEqual(client.post("/only-bytes").json, {"size": None})
        self.assertEqual(
            client.post(
                "/only-bytes", data=b"bear", headers={"content-type": "application/octet-stream"}
            ).json,
            {"size": 4},
        )

    def test_async_lifespan_preserves_contextvar_and_shared_loop_in_test_client(self) -> None:
        from contextvars import ContextVar

        marker: ContextVar[str | None] = ContextVar("lifespan_marker", default=None)
        loops: list[asyncio.AbstractEventLoop] = []

        @asynccontextmanager
        async def lifespan(app: Higuma):
            token = marker.set("active")
            loops.append(asyncio.get_running_loop())
            try:
                yield {"ready": True}
            finally:
                loops.append(asyncio.get_running_loop())
                marker.reset(token)

        app = Higuma(__name__, static_folder=None, lifespan=lifespan)

        @app.on_startup
        async def startup() -> None:
            loops.append(asyncio.get_running_loop())

        @app.on_shutdown
        async def shutdown() -> None:
            loops.append(asyncio.get_running_loop())

        @app.get("/loop")
        async def check_loop() -> dict[str, bool]:
            loops.append(asyncio.get_running_loop())
            return {"same": loops[-1] is loops[0]}

        with app.test_client() as client:
            self.assertEqual(client.get("/loop").json, {"same": True})

        self.assertEqual(len(loops), 5)
        self.assertTrue(all(loop is loops[0] for loop in loops))
        self.assertIsNone(marker.get())

    def test_optional_constrained_none_and_openapi_file_list_tuple_enum(self) -> None:
        from enum import Enum

        from higuma import Parameter

        class Ratio(Enum):
            HALF = 0.5
            WHOLE = 1.0

        class Mixed(Enum):
            TEXT = "a"
            NUMBER = 1

        @dataclass
        class OptionalConstrained:
            code: Annotated[str | None, Parameter(min_length=3, pattern=r"^[A-Z]+$")] = None
            count: Annotated[int | None, Parameter(ge=0)] = None

        app = self.make_app()

        @app.post("/optional-constrained")
        def optional_constrained(
            payload: Annotated[OptionalConstrained, Body()],
        ) -> dict[str, object]:
            return {"code": payload.code, "count": payload.count}

        @app.post("/upload-many")
        def upload_many(
            files: Annotated[list[UploadFile], File()],
        ) -> dict[str, int]:
            return {"count": len(files)}

        client = app.test_client()
        self.assertEqual(
            client.post("/optional-constrained", json={"code": None, "count": None}).json,
            {"code": None, "count": None},
        )
        self.assertEqual(
            client.post("/optional-constrained", json={"code": "ab", "count": 1}).status_code,
            422,
        )

        from higuma.openapi import schema_for

        self.assertEqual(
            schema_for(tuple[int]),
            {"type": "array", "prefixItems": [{"type": "integer"}], "minItems": 1, "maxItems": 1},
        )
        self.assertEqual(schema_for(Ratio), {"enum": [0.5, 1.0], "type": "number"})
        self.assertEqual(schema_for(Mixed), {"enum": ["a", 1]})
        upload_schema = app.openapi()["paths"]["/upload-many"]["post"]["requestBody"]["content"][
            "multipart/form-data"
        ]["schema"]["properties"]["files"]
        self.assertEqual(
            upload_schema,
            {"type": "array", "items": {"type": "string", "format": "binary"}},
        )

    def test_parameter_validation_intenum_rfc3339_and_openapi_disambiguation(self) -> None:
        import typing
        from datetime import date, datetime
        from enum import IntEnum
        from typing import Any

        from higuma import Parameter
        from higuma.parameters import _convert_value

        class Priority(IntEnum):
            LOW = 1
            HIGH = 2

        for bad_kwargs in (
            {"min_length": True},
            {"max_length": True},
            {"ge": True},
            {"ge": float("nan")},
            {"lt": float("inf")},
            {"ge": 10, "le": 5},
            {"gt": 5, "le": 5},
            {"ge": 5, "lt": 5},
            {"gt": 5, "lt": 5},
        ):
            with self.assertRaises(ValueError):
                Parameter(**bad_kwargs)  # type: ignore[arg-type]

        with self.assertRaises(TypeError):
            _convert_value("not-null", type(None))
        self.assertIsNone(_convert_value(None, type(None)))
        with self.assertRaises(TypeError):
            _convert_value(date(2026, 11, 22), datetime)

        def make_order_item():
            @dataclass
            class Item:
                sku: str

            return Item

        def make_stock_item():
            @dataclass
            class Item:
                quantity: int

            return Item

        OrderItem = make_order_item()
        StockItem = make_stock_item()

        app = self.make_app()

        @app.get("/priority")
        def priority_route(
            level: Annotated[Priority, QueryParam()],
            when: Annotated[datetime | None, QueryParam()] = None,
            extra: typing.Optional[Annotated[Any, QueryParam()]] = None,  # noqa: UP045 - verify Optional[Annotated[Any, ...]]
        ) -> dict[str, object]:
            return {
                "level": int(level),
                "tz": when.tzinfo is not None if when else None,
                "extra": extra,
            }

        @app.post("/order")
        def create_order(payload: Annotated[OrderItem, Body()]) -> OrderItem:
            return payload

        @app.post("/stock")
        def update_stock(payload: Annotated[StockItem, Body()]) -> StockItem:
            return payload

        @app.post("/raw-text")
        def raw_text(text: Annotated[str, Body()]) -> dict[str, str]:
            return {"text": text}

        @app.post("/raw-bytes")
        def raw_bytes(raw: Annotated[bytes | None, Body()] = None) -> dict[str, int]:
            return {"len": len(raw or b"")}

        client = app.test_client()
        self.assertEqual(
            client.get("/priority?level=2&when=2026-11-22T12:00:00z&extra=hi").json,
            {"level": 2, "tz": True, "extra": "hi"},
        )
        self.assertEqual(client.get("/priority?level=HIGH").json["level"], 2)
        with self.assertRaises(ValueError):
            _convert_value(True, Priority)

        spec = app.openapi()
        schemas = spec["components"]["schemas"]
        self.assertIn("Item", schemas)
        self.assertIn("Item_2", schemas)
        self.assertIn("sku", schemas["Item"]["properties"])
        self.assertIn("quantity", schemas["Item_2"]["properties"])
        self.assertIn("text/plain", spec["paths"]["/raw-text"]["post"]["requestBody"]["content"])
        self.assertIn(
            "application/octet-stream",
            spec["paths"]["/raw-bytes"]["post"]["requestBody"]["content"],
        )

    def test_parameter_coercion_decimal_no_deepcopy_and_openapi_metadata(self) -> None:
        from datetime import datetime
        from decimal import Decimal
        from typing import Any

        from higuma import ServerSentEvent
        from higuma.parameters import (
            _convert_value,
            _validate_constraints,
            serialize_json_value,
        )

        class NoCopy:
            def __deepcopy__(self, memo: Any) -> Any:
                raise AssertionError("serialize_json_value must not deepcopy")

            def __str__(self) -> str:
                return "nocopy"

        @dataclass
        class Quote:
            price: Decimal
            marker: Any

        serialized = serialize_json_value(Quote(price=Decimal("19.95"), marker=NoCopy()))
        self.assertEqual(serialized["price"], "19.95")
        self.assertIsInstance(serialized["marker"], NoCopy)
        self.assertIn(b'"19.95"', ServerSentEvent(data={"price": Decimal("19.95")}).encode())

        with self.assertRaises(TypeError):
            _convert_value(20260102, date)
        with self.assertRaises(TypeError):
            _convert_value(20260102, datetime)
        with self.assertRaises(TypeError):
            _convert_value(12345, UUID)
        with self.assertRaises(TypeError):
            _validate_constraints(True, QueryParam(ge=0))

        class Color(Enum):
            RED = "red"
            BLUE = "blue"

        app = Higuma(__name__, static_folder=None)
        custom_responses = {"200": {"description": "Custom OK"}}

        @app.post("/paint/<item_id>", responses=custom_responses)
        def paint(
            item_id: Annotated[str, PathParam(description="Item identifier", deprecated=True)],
            color: Annotated[Literal[Color.RED, Color.BLUE], QueryParam()],
            note: Annotated[str, Body(description="Paint note")],
        ) -> dict[str, str]:
            return {"id": item_id, "color": color.value, "note": note}

        @app.delete("/paint/<item_id>", status_code=204)
        def remove(item_id: str) -> None:
            return None

        spec = app.openapi()
        self.assertNotIn("422", custom_responses)
        post_op = spec["paths"]["/paint/{item_id}"]["post"]
        self.assertIn("422", post_op["responses"])
        params = {(p["in"], p["name"]): p for p in post_op["parameters"]}
        self.assertEqual(params[("path", "item_id")]["description"], "Item identifier")
        self.assertTrue(params[("path", "item_id")]["deprecated"])
        self.assertEqual(
            params[("query", "color")]["schema"],
            {"enum": ["red", "blue"], "type": "string"},
        )
        self.assertEqual(post_op["requestBody"]["description"], "Paint note")
        delete_op = spec["paths"]["/paint/{item_id}"]["delete"]
        self.assertNotIn("content", delete_op["responses"]["204"])


if __name__ == "__main__":
    unittest.main()
