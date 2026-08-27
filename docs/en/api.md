# API reference

Unless noted otherwise, every name below is importable with `from higuma import ...`.
Use `higuma.__version__` to inspect the installed version.

## Application and routing

### `Higuma`

```python
Higuma(
    import_name,
    *,
    template_folder="templates",
    static_folder="static",
    static_url_path="/static",
    max_content_length=8 * 1024 * 1024,
    debug=False,
    openapi_url="/openapi.json",
    docs_url="/docs",
    lifespan=None,
)
```

Main methods:

- `route(rule, *, methods=None, endpoint=None, response_model=None, status_code=None, ...)`
- `get`, `post`, `put`, `patch`, and `delete` shortcuts
- `add_url_rule(rule, *, endpoint, view_func, methods=None, ...)`
- `websocket(rule, *, endpoint=None, allowed_origins=None)`
- `register_blueprint(blueprint, *, url_prefix=None, name_prefix="")`
- `url_for(endpoint, **values)`
- `before_request`, `after_request`, `middleware`, and `errorhandler`
- `on_startup`, `on_shutdown`, and `context_processor`
- `add_middleware(middleware, *args, **kwargs)`
- `render_template`, `jsonify`, and `make_response`
- `mount_wsgi` and `mount_asgi`
- `openapi()`, `clear_template_cache()`, and `init_database()`
- `test_client()`
- `run(host="127.0.0.1", port=8000, *, workers=0, processes=1, ...)`

OpenAPI route options are `summary`, `description`, `tags`, `responses`, `request_body`,
`response_model`, `status_code`, `operation_id`, `include_in_schema`, and `openapi_extra`.
`response_model` also validates and converts output at runtime while filtering undeclared fields.

`current_app` and `request` are context-local proxies to the active application and request.
Access outside a request context raises `RuntimeError`. Use `app.config` for configuration and
`app.state` for shared state initialized by the lifespan.

### `Blueprint`

`Blueprint(name, import_name, *, url_prefix="")` groups routes. It provides `route`, the HTTP
shortcuts, and `websocket`; register it with `register_blueprint()`.

### `View` / `MethodView`

`View.as_view(name, *class_args, **class_kwargs)` converts a class into a view function.
Implement `dispatch_request()` on `View`, or `get()`, `post()`, and other HTTP methods on
`MethodView`. `MethodView` derives its allowed methods from implemented method names.

## Typed inputs and dependency injection

Declare an input source and constraints with `typing.Annotated`; Higuma extracts and converts the
value before calling the handler.

```python
from typing import Annotated

from higuma import Body, Depends, Header, PathParam, QueryParam


def page_size(limit: Annotated[int, QueryParam(ge=1, le=100)] = 20):
    return limit


@app.post("/items/<uuid:item_id>", response_model=ItemOutput, status_code=201)
def create_item(
    item_id: Annotated[UUID, PathParam()],
    payload: Annotated[ItemInput, Body()],
    limit: Annotated[int, Depends(page_size)],
    trace_id: Annotated[str | None, Header("x-trace-id")] = None,
):
    ...
```

- `QueryParam`, `Header`, `Cookie`, `PathParam`, `Body`, `Form`, and `File` derive from `Parameter`.
- Common options are `alias`, `title`, `description`, `deprecated`, `min_length`, `max_length`,
  `pattern`, `ge`, `gt`, `le`, and `lt`.
- Recursive conversion supports primitives, `UUID`, date/time, `Enum`, `Literal`, unions,
  optional values, collections, dataclasses, and `TypedDict`.
- Collection types receive repeated query, form, or file values.
- `Depends(callable, use_cache=True)` resolves nested sync, async, and yield dependencies.
  Yield-dependency cleanup runs in reverse order after the response body and background tasks.
- Replace dependencies in tests with `app.dependency_overrides[dependency] = replacement`.
- Unannotated path arguments, `Request`, and `BackgroundTasks` can also be injected.

Invalid input becomes a JSON 422 `RequestValidationError`. Details contain `loc`, `msg`, `type`,
and `input` only when safe. Password, token, secret, and Cookie values are redacted. An invalid
`response_model` output raises `ResponseValidationError` and returns 500 to the client.

## Request

### `Request`

- `method`, `path`, `query_string`, `scheme`, `host`, `base_url`, and `url`
- `args` / `query`: a duplicate-preserving `MultiDict`
- `headers`: case-insensitive `Headers`
- `cookies`: read-only mapping
- `json` / `get_json(force=False, silent=False)`
- `form`, `files`, `body`, `text`, and `get_data(as_text=False)`
- `path_params` / `view_args`
- `client_addr` / `remote_addr`
- `state`, `session`, and `user`
- `raw_headers`: duplicate-preserving byte header pairs

### `Headers` / `MultiDict`

`Headers` compares names case-insensitively. `MultiDict` provides `get()`, `getlist()`,
`items(multi=False)`, `keys()`, `values()`, and `to_dict(flat=True)`.

### `UploadFile`

An `UploadFile` has `filename`, `content_type`, `headers`, `field_name`, and `size`, plus `read()`,
`seek()`, `getvalue()`, and `save(destination)`. `secure_filename(filename, fallback="upload")`
removes directory components and unsafe characters. The application must still enforce unique
names, extensions, content checks, and quotas.

## Response

### Basic responses

- `Response(body=b"", status=200, headers=None, media_type=None, background=None)`
- `HTMLResponse`, `PlainTextResponse`, and `JSONResponse`
- `RedirectResponse(location, status=302, ...)`
- `TemplateResponse(template, context=None, ...)`
- `FileResponse(path, *, filename=None, as_attachment=False, offset=0, length=None, ...)`

Full-file `FileResponse` and `send_file()` values automatically handle ETags,
`If-None-Match`, and single byte ranges for GET/HEAD. Explicit `offset` / `length`
values stream the selected range directly from Rust.

`Response` exposes `status_code` / `status`, `headers`, `media_type`, `body`, and `history`.
It provides `append_header()`, `get_json()`, `set_cookie()`, and `delete_cookie()`.
`set_cookie(..., partitioned=True)` requires Python 3.14 or newer and `secure=True`.

Helpers are `jsonify()`, `make_response()`, `redirect()`, `render_template()`, and `send_file()`.
A handler may also return strings, bytes-like objects, JSON-compatible values, `None`, or
`(body, status[, headers])`. Dataclasses, enums, UUIDs, and dates serialize to JSON.

### Streaming and SSE

`StreamingResponse(content, *, media_type=..., content_length=None, background=None)` sends a sync
or async iterable one chunk at a time. Chunks must be strings or bytes-like values. The request
context captured at response creation remains available until the stream finishes. Set
`content_length` only when it is known.

`EventSourceResponse(content, ...)` creates an SSE response. Yield values directly, or use
`ServerSentEvent(data, event=None, id=None, retry=None, comment=None)`. SSE configures
`text/event-stream`, `Cache-Control: no-cache`, and a buffering opt-out, and is excluded from gzip.

### Background tasks

`BackgroundTask(func, *args, **kwargs)` runs a sync or async callable after the response body is
sent. `BackgroundTasks` uses `add_task()` to register multiple tasks and can be injected into a
handler. Tasks preserve the request context captured at registration and run in order; later tasks
do not run after a failure. Use a durable job queue for heavy work, retries, or delivery guarantees.

## Lifespan, middleware, and sessions

`Higuma(..., lifespan=context_manager)` enters once per worker and exits at shutdown. Sync and async
context managers are supported; a yielded mapping is merged into `app.state`. Existing
`on_startup` / `on_shutdown` hooks run in the same lifecycle. In tests, use
`with app.test_client() as client:` to execute the lifecycle.

- `SessionMiddleware(secret_key, *, cookie_name="higuma_session", max_age=..., secure=False, ...)`
- `Session`: dict-compatible storage with `permanent` and `modified`
- `CORSMiddleware`
- `SecurityHeadersMiddleware`
- `TrustedHostMiddleware`
- `ProxyHeadersMiddleware(trusted_proxies=("127.0.0.1", "::1"))`
- `RateLimitMiddleware(limit=100, window=60.0, *, key=None, max_keys=10000)`

Trust proxy headers only when the direct peer is in `trusted_proxies`.

## WebSocket

`WebSocket` provides `send()`, `send_text()`, `send_bytes()`, `send_json()`, `receive()`,
`receive_text()`, `receive_bytes()`, `receive_json()`, and `close()`. Disconnects raise
`WebSocketDisconnect(code=1000, reason="")`. Omitting `allowed_origins` on a route allows only the
same origin.

## SQLite ORM

- `Database(url="sqlite:///higuma.db")`: `connect()`, `create_all()`, `drop_all()`, and `session()`
- `Model(**values)`: `to_dict()`
- `DatabaseSession`: `add()`, `save()`, `delete()`, `query()`, `execute()`, `commit()`, `rollback()`
- `Query`: `get()`, `filter_by()`, `order_by()`, `offset()`, `limit()`, `first()`, `all()`, `count()`,
  `delete()`, and `delete_all()`
- Fields: `Field`, `Integer`, `Float`, `String`, `Boolean`, `Date`, `DateTime`, and `Blob`

Common field options are `primary_key`, `nullable`, `unique`, `default`, and `index`. `Integer`
also accepts `autoincrement`; `String` accepts `length`. `Query.delete()` refuses an unfiltered
delete. The built-in ORM is a small synchronous SQLite data mapper.

## Authentication and security

- `AuthManager`: `load_user()`, `login_user()`, `logout_user()`, `confirm_login()`, and decorators
- `current_user` / `AnonymousUser`
- `login_required`, `fresh_login_required`, `roles_required`, and `permissions_required`
- `PasswordHasher`: scrypt `hash()`, `verify()`, and `needs_rehash()`
- `TokenSigner`: `dumps()` and `loads(max_age=None)`
- `CSRFProtection` / `csrf_token(field_name=None)`
- `OAuth2Client`: `google()`, `line()`, `discord()`, `authorization_url()`, `fetch_token()`,
  `userinfo()`, and `validate_state()`; sessions carry state, PKCE, and OIDC nonce data
- `generate_user_id(prefix="usr", entropy_bytes=18)` / `validate_user_id()`

## Exceptions and operations

Call `abort(status_code, detail=None, headers=None)` or raise an `HTTPException` subclass:

`BadRequest`, `Unauthorized`, `Forbidden`, `NotFound`, `MethodNotAllowed`, `Conflict`,
`RequestEntityTooLarge`, `UnsupportedMediaType`, `RangeNotSatisfiable`, `TooManyRequests`, and
`InternalServerError`.

`Supervisor(app, *, host="127.0.0.1", port=8000, processes=2, ...)` manages multiple worker
processes, health checks, and restart limits. It is normally used through
`app.run(processes=..., app_ref="module:app")` or the CLI.

See [Examples](examples.md) and the
[repository examples](https://github.com/Madoa5561/higuma/tree/main/examples) for runnable code.
