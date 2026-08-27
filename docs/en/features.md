# Feature guide

## Routing and handlers

```python
@app.route("/items/<int:item_id>", methods=("GET", "POST"))
def item(item_id: int):
    return {"item_id": item_id, "method": request.method}
```

`get`, `post`, `put`, `patch`, and `delete` are shortcuts for `route`. Static
paths take priority over dynamic paths. Registering the same path and method,
or the same endpoint twice, raises `ValueError`.

Handlers may be synchronous functions or `async def`. Path parameters are
passed to arguments with matching names, and an argument named `request`
receives the `Request` object. Using the `from higuma import request` proxy
usually keeps handler signatures clearer.

```python
@app.get("/wait")
async def wait():
    await some_async_operation()
    return {"done": True}
```

Available converters are `string`, `int`, `float`, `uuid`, and `path`.

Use `View` or `MethodView` to group view logic in a class. `MethodView` derives
its allowed methods from implemented `get()`, `post()`, and similar methods.

## Typed inputs and dependencies

```python
from typing import Annotated

from higuma import Body, Depends, Header, PathParam, QueryParam


def pagination(limit: Annotated[int, QueryParam(ge=1, le=100)] = 20):
    return limit


@app.post("/items/<uuid:item_id>", response_model=ItemOutput, status_code=201)
def create_item(
    item_id: Annotated[UUID, PathParam()],
    payload: Annotated[ItemInput, Body()],
    limit: Annotated[int, Depends(pagination)],
    trace_id: Annotated[str | None, Header("x-trace-id")] = None,
):
    return {"id": item_id, **payload.__dict__}
```

`QueryParam`, `Header`, `Cookie`, `PathParam`, `Body`, `Form`, and `File` declare
the source and constraints. Conversion recursively supports dataclasses,
TypedDicts, UUIDs, dates, datetimes, enums, literals, collections, and optional
values. Invalid inputs return structured JSON 422 responses.

`Depends` supports nested dependencies, sync and async callables, yield cleanup,
and per-request caching. Tests can replace dependencies through
`app.dependency_overrides`. `response_model` validates and converts runtime output
and filters undeclared fields.

## Request data

```python
@app.post("/search")
def search():
    tags = request.args.getlist("tag")
    payload = request.get_json(silent=True)
    return {
        "tags": tags,
        "payload": payload,
        "client": request.remote_addr,
    }
```

- `args` / `query`: a `MultiDict` that preserves repeated keys
- `headers`: case-insensitive `Headers`
- `json` / `get_json()`: JSON body; a wrong media type raises `UnsupportedMediaType`
- `form` / `files`: URL-encoded or multipart form data
- `body` / `get_data()` / `text`: the raw body
- `cookies`: a read-only mapping
- `path_params` / `view_args`: converted path parameters
- `client_addr` / `remote_addr`: the direct peer, changed only by trusted proxy middleware
- `state`: a request-local dictionary shared by middleware and handlers
- `session` / `user`: populated by the corresponding middleware

`raw_headers` is a duplicate-preserving sequence of byte pairs for WSGI/ASGI
interoperability.

## Responses

A handler may return `str`, bytes-like data, `dict`, `list`, `None`, a
`Response`, or `(body, status[, headers])`.

```python
@app.post("/items")
def create_item():
    response = app.jsonify({"id": 1, "status": "created"}, status=201)
    response.set_cookie("notice", "created", httponly=True, samesite="Lax")
    return response
```

Choose `HTMLResponse`, `PlainTextResponse`, `JSONResponse`, `RedirectResponse`,
`FileResponse`, or `TemplateResponse` when the representation should be
explicit. Status codes and header names/values are validated both at creation
and after middleware mutation. Do not set `Content-Length` manually; leave it
to the Rust core.

`StreamingResponse` sends a sync or async iterable without buffering the whole
body. The request context captured at creation remains available until iteration
finishes. `EventSourceResponse` and `ServerSentEvent` format SSE fields and set
no-cache and proxy-buffering headers. `BackgroundTask` / `BackgroundTasks` run
small sync or async work after the body is sent. Use an external job queue when
durability or retries matter.

## Blueprints and URL generation

```python
api = Blueprint("api", __name__, url_prefix="/api")


@api.get("/users/<int:user_id>")
def user(user_id: int):
    return {"id": user_id}


app.register_blueprint(api)
profile_url = app.url_for("api.user", user_id=42)
```

Registration can override `url_prefix` and add an endpoint namespace with
`name_prefix`. Blueprints support both HTTP and WebSocket routes.

## Hooks, middleware, and lifecycle

```python
@app.before_request
def start_timer():
    request.state["started"] = time.monotonic()


@app.after_request
def add_timing(response):
    elapsed = time.monotonic() - request.state["started"]
    response.headers["server-timing"] = f"app;dur={elapsed * 1000:.2f}"
    return response


@app.errorhandler(404)
def not_found(error):
    return {"error": error.detail}, 404
```

Before hooks run in registration order; after hooks run in reverse. A response
from a before hook skips the handler. The first middleware added is outermost
on the request path and returns last on the response path. Register middleware
that needs a session inside, and therefore after, `SessionMiddleware`.

`Higuma(..., lifespan=context_manager)` enters and exits a sync or async context
manager once per worker, merging a yielded mapping into `app.state`.
`on_startup` and `on_shutdown` run in the same lifecycle and accept sync or async
functions. A mapping from `context_processor` is merged into the context used by
`app.render_template()`. When sharing loop-bound async clients, the application
must preserve affinity with the event loop that created the client.

## Templates, static files, and downloads

```python
@app.context_processor
def globals_for_templates():
    return {"site_name": "higuma example"}


@app.get("/")
def page():
    return app.render_template("index.html", title="Home")
```

The MiniJinja environment and compiled templates are shared in Rust. Call
`app.clear_template_cache()` to force a reload during development.

When `static_folder` is enabled, files are served under `static_url_path`.
Static responses, `send_file()`, and full-file `FileResponse` values support
ETags, `If-None-Match`, and single byte ranges for GET/HEAD. Rust seeks and
streams the file or selected range without reading it into Python memory.

## Multipart uploads

```python
from pathlib import Path
from secrets import token_hex


@app.post("/upload")
def upload():
    uploaded = request.files["file"]
    destination = Path("uploads") / f"{token_hex(8)}-{uploaded.filename}"
    uploaded.save(destination)
    return {"name": destination.name, "size": uploaded.size}, 201
```

`UploadFile.filename` is already normalized with `secure_filename()`. The
application must still use unique names and enforce allowed extensions/media
types, storage quotas, and malware scanning. `Higuma(max_content_length=...)`
limits the entire request.

## WebSockets

```python
@app.websocket(
    "/ws/<string:room>",
    allowed_origins=("https://example.com",),
)
def chat(ws, room):
    while True:
        ws.send_json({"room": room, "message": ws.receive_json()})
```

Origins default to same-origin. Queues are bounded, message size follows
`max_content_length`, and authentication decorators run before the HTTP 101
upgrade. Handle `WebSocketDisconnect` when a peer disconnects.

## WSGI and ASGI mounts

```python
app.mount_wsgi("/legacy", flask_app, name="legacy")
app.mount_asgi("/service", asgi_app, name="service")
```

The mounted application receives the path without the prefix. These are HTTP
mounts; ASGI WebSocket and lifespan scopes are not forwarded. Use a unique
name to avoid endpoint collisions.

## OpenAPI

```python
from dataclasses import dataclass


@dataclass
class CreateUser:
    name: str


@app.post("/users", response_model=CreateUser, status_code=201, tags=("users",))
def create_user(payload: Annotated[CreateUser, Body()]):
    return payload
```

`/openapi.json` and `/docs` are enabled by default. Annotations for primitives,
containers, unions/optionals/literals, dataclasses, TypedDicts, enums, UUIDs,
and dates/datetimes are converted to OpenAPI 3.1 schemas.

Parameter markers such as `Annotated[..., Body()]` drive both runtime validation
and OpenAPI. `response_model` also validates runtime output. The legacy
`request_body=` option and a return annotation by itself remain schema metadata;
they do not enable runtime validation.

## SQLite ORM

```python
class User(Model):
    id = Integer(primary_key=True, autoincrement=True)
    email = String(nullable=False, unique=True, index=True)


db = Database("sqlite:///app.db")
db.create_all(User)
with db.session() as session:
    session.add(User(email="bear@example.com"))
```

A successful session block commits; an exception rolls back. `filter_by()`
binds values as parameters, and `order_by()` accepts model field names only.
Use `offset()` and `limit()` for pagination. Unfiltered `delete()` is rejected;
use `delete_all()` only for an intentional full-table delete.

The built-in ORM is a small SQLite-only data mapper. Consider a specialized
database library when you need relationships, migrations, async queries, or a
connection pool.

## Test client

```python
client = app.test_client()
response = client.post(
    "/upload",
    data={"caption": "bear"},
    files={"file": ("bear.txt", b"hello", "text/plain")},
)
assert response.status_code == 201
```

The client persists cookies between requests and supports JSON, forms,
multipart data, HEAD, OPTIONS, and redirect history. `with app.test_client() as
client:` also runs lifespan and startup/shutdown hooks. It does not use a real
socket, so verify WebSockets, wire-level framing, proxies, and the supervisor
against a real server.
