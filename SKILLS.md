# Using higuma with coding agents and LLMs

This file is the machine-readable working guide for creating and modifying
higuma applications. Read it before generating code.

## Project identity

- Package: `higuma`
- Import: `from higuma import Higuma`
- Runtime: Python 3.10+ with a Rust `axum` / `Tokio` HTTP core
- Default address: `http://127.0.0.1:8000`
- Documentation: <https://higuma.moyashi.xyz/en/>
- Source: <https://github.com/Madoa5561/higuma>

## Minimal application

```python
from higuma import Higuma

app = Higuma(__name__)


@app.get("/")
def index():
    return {"framework": "higuma"}


if __name__ == "__main__":
    app.run()
```

Install and run:

```bash
pip install higuma
python app.py
```

## API selection guide

| Goal | Use |
| --- | --- |
| Nested components / scoped static and templates | `Blueprint(..., template_folder=..., static_folder=...)`, `parent.register_blueprint(child)` (0.9+) |
| Application commands | `@app.cli.command()`, `@blueprint.cli.command()`, `app.test_cli_runner()` (0.9+) |
| Async HTTP/WS/lifespan tests | `async with app.test_async_client()` (0.9+) |
| Bounded large-body reading | `request.stream`, `iter_body` / `aiter_body`; ingress spools before dispatch (0.9+) |
| SQLAlchemy / Redis integration | Optional `integrations` extra and explicit transactions/lifespan (0.9+) |
| HTTP route | `@app.get`, `post`, `put`, `patch`, `delete` |
| Export to ASGI server | `asgi_app = app.asgi_app` (0.8+, asyncio HTTP/WS/lifespan) |
| Export to WSGI server | `wsgi_app = app.wsgi_app` (0.8+, HTTP only) |
| Context-only utility test | `with app.test_request_context(path, **kwargs):` (0.7+) |
| Request-local response callback | `@after_this_request` (0.7+) |
| Scoped hooks / errors / template values | Blueprint hooks, `errorhandler`, `context_processor` (0.7+) |
| Request cleanup | `@app.teardown_request` / `@blueprint.teardown_request` (0.7+) |
| Realtime connection | `@app.websocket("/ws")` |
| HTML SSR | `app.render_template("page.html", value=value)` |
| CSR / SPA history fallback | `app.mount_spa("/app", build_dir)` (0.6+) |
| Multiple static roots / HTML indexes | `app.mount_static("/files", directory, html=True)` |
| Vite hashed assets / CSS / module preloads | `ViteManifest(path, base_url="/build/").tags("index.html")` |
| JSON | return `dict`/`list` or call `app.jsonify(...)` |
| Typed query/header/cookie/path | `Annotated[T, QueryParam/Header/Cookie/PathParam(...)]` |
| Typed JSON/form/file | `Annotated[T, Body/Form/File(...)]` |
| Dependency injection | `Annotated[T, Depends(callable)]` |
| Validated output | route option `response_model=Type` |
| Streaming / SSE | `StreamingResponse` / `EventSourceResponse` |
| After-response work | injected `BackgroundTasks` or `BackgroundTask` |
| Class-based routes | `View` or `MethodView` with `add_url_rule` |
| Application lifecycle | `Higuma(..., lifespan=context_manager)` |
| Uploaded files, dynamic access | `request.files["file"]` |
| Form values, dynamic access | `request.form["name"]` |
| OpenAPI metadata | route options `summary`, `tags`, `responses` |
| OpenAPI JSON / UI | `/openapi.json` and `/docs` |
| Existing WSGI app | `app.mount_wsgi("/legacy", wsgi_app)` |
| Existing ASGI app | `app.mount_asgi("/service", asgi_app)` |
| SQLite ORM | `Database`, `Model`, and field classes |
| Login sessions | `AuthManager` and `@auth.login_required` |
| Password storage | `PasswordHasher`, never a plain hash |
| Signed expiring data | `TokenSigner` |
| CSRF defense | `SessionMiddleware` then `CSRFProtection` |
| Abuse prevention | `RateLimitMiddleware(max_keys=10_000)` |
| Trusted reverse proxy | `ProxyHeadersMiddleware` with exact peer CIDRs |
| OAuth | `OAuth2Client.google`, `.line`, or `.discord` |
| Multi-process | `higuma run module:app --processes 4 --max-connections 1024` |

## Request and response rules

- Prefer `Annotated` parameter markers for public API boundaries that benefit
  from conversion, structured 422 errors, and generated schemas.
- Import the request proxy with `from higuma import request` for dynamic access.
- Use `request.args`, `request.json`, `request.form`, and `request.files` when
  the shape is intentionally dynamic.
- A route may return `str`, `bytes`, `dict`, `list`, `Response`, or
  `(body, status, headers)`.
- Use typed path converters: `<int:id>`, `<float:value>`, `<uuid:id>`,
  `<path:filename>`, and `<string:name>`.
- Do not manually serialize JSON unless a custom encoding is required.
- Use `response_model` when output must be validated or filtered. A return
  annotation by itself is schema metadata.
- Use `StreamingResponse` for incremental sync/async output and
  `EventSourceResponse` for SSE. Do not buffer streams into a list first.
- Keep background tasks short and non-durable; use a job queue for retries or
  delivery guarantees.
- Do not access private `_core` APIs from application code.
- Use `request.raw_headers` only when duplicate header byte pairs are required
  for ASGI/WSGI interoperability.
- `request.values` gives query priority over form (GET uses query only).
  Within each source, duplicate single-value lookups remain last-value, unlike Flask.
- Response `get_json()` checks MIME in 0.7+. Use `force=True` only for intentionally
  mislabeled bodies. Request JSON rejects invalid Unicode rather than replacing it.
- `after_this_request` callbacks run in registration order, before Blueprint and
  app after hooks. Session cookies are saved after those hooks, before teardown.
- Cleanup must tolerate middleware short-circuits. Open new resources inside lazy
  streams/background work rather than reusing teardown-closed resources.
- Context helpers do not dispatch hooks/middleware or load sessions. They yield
  Higuma Request values, not Flask RequestContext wrappers; `request_context`
  accepts Request, not a WSGI environ. Test client contexts run lifespan, not
  preserved request contexts. See docs/testing.md and docs/en/testing.md.

## Server interface boundaries (0.8+)

Use explicit `app.asgi_app` / `app.wsgi_app`, not a callable app overload.
The optional `asgi` extra supplies Uvicorn/websockets; base dependencies stay empty.
ASGI bridges the synchronous pipeline through a dedicated bounded executor;
0.10+ can directly execute eligible async HTTP routes without sync hooks/middleware/DI.
Other async handlers still occupy coordination workers. Async lifespan and request
awaitables use the server loop; use one instance per active loop. WSGI has no
automatic lifespan or WebSocket support. Standard middleware wraps the exported
entry point; Higuma's synchronous `call_next` is not an async middleware chain API.
Async WS handlers use `asend_*`, `areceive*`, `aclose`, also supported natively.
Root prefixes come from trusted server scope/environ, not arbitrary forwarded headers.
HTTP ingress completes before dispatch; 0.9 adds bounded disk spooling and stored-body
iterators, not live network-body iteration. Mounted apps remain buffered HTTP-only.
External transport owns TLS/compression/workers and does not inherit Rust server
performance guarantees. See docs/server-interfaces.md and its English mirror.

## Development and ecosystem boundaries (0.9+)

0.10 adds AsyncSQLAlchemyExtension (ASGI-owned loop, explicit transactions),
RedisSessionStore/RedisSessionMiddleware (opaque signed IDs, CAS/revocation),
RedisRateLimitMiddleware (fixed windows, fail closed), synchronous lifecycle Signals,
Observability (bounded local metrics, no automatic exports), and HealthChecks.
See docs/operations.md and its English mirror. Use explicit app contexts in startup
probes that access extensions. Do not mix cookie and Redis session middleware.
Signals are Higuma objects, not Flask/Blinker objects. Prefer an external observer
wrapper for full ASGI stream timing; native middleware measures dispatch calls only.

See docs/developer-experience.md, integrations.md, uploads.md and English mirrors.
Before hooks inherit parent-to-child; after/teardown inherit child-to-parent.
Templates search app first, then registered components: namespace filenames.
CLI uses argparse scalar arguments, not Click compatibility. Commands have app
context but not automatic lifespan. ASGI test clients require asyncio context for
lifespan/state; keep real-server tests too. SQLAlchemy dependency sessions close
without implicit commit: finish explicit transactions before returning success.
Async Redis requires ASGI; native/WSGI use synchronous clients. Shutdown hooks
also run after partial startup failure and must tolerate uninitialized resources.
Keep request bodies/files request-scoped and apply disk quota plus transport timeouts.
Do not claim general speed advantages: reproduce bench/suite.py and publish raw
results, configuration, error counts, latency and client/host limitations.

## Frontend application boundaries

Keep frontend base URLs/router basenames aligned with the mount prefix. SPA
fallback is only for missing extensionless GET/HEAD paths accepting HTML; reserve
all API and asset prefixes. Do not catch application errors and return the SPA
shell. Use immutable caches only for content-addressed assets. Never serve
credentials, source trees, databases, or writable upload stores as public roots.
Vite/React/Vue compilers, HMR, Node SSR, service-worker lifecycle, durable jobs,
and distributed state are external integrations, not built-in promises.
See docs/frontend.md and docs/capabilities.md (English under docs/en/).

## WebSocket pattern (native)

```python
from higuma import Higuma, WebSocketDisconnect

app = Higuma(__name__)


@app.websocket("/ws", allowed_origins=("https://example.com",))
def socket(ws):
    try:
        while True:
            ws.send_json({"echo": ws.receive_json()})
    except WebSocketDisconnect:
        pass
```

Available methods are `send_text`, `send_bytes`, `send_json`, `receive_text`,
`receive_bytes`, `receive_json`, and `close`.

WebSocket origins default to same-origin. Keep explicit production origins,
and do not use `"*"` for cookie-authenticated sockets. Authentication
decorators are evaluated before the protocol upgrade.

## Database pattern

```python
from higuma import Database, Integer, Model, String

db = Database("sqlite:///app.db")


class User(Model):
    id = Integer(primary_key=True, autoincrement=True)
    email = String(nullable=False, unique=True, index=True)


db.create_all(User)

with db.session() as session:
    user = session.add(User(email="bear@example.com"))

with db.session() as session:
    user = session.query(User).filter_by(email="bear@example.com").first()
```

- Keep one session within one `with db.session()` block.
- A successful block commits; an exception rolls back.
- Use `filter_by()` so values are always passed through parameter binding.
- Use `offset()` and `limit()` for pagination.
- `delete()` requires a filter. Use `delete_all()` only for an intentional
  full-table delete.

## Authentication and security rules

- Load `HIGUMA_SECRET_KEY`, OAuth client IDs, and OAuth client secrets from the
  environment or a secret manager.
- Require at least 32 bytes of entropy-bearing session/token secret material.
- Never commit credentials, database passwords, tokens, or production cookies.
- Store passwords with `PasswordHasher.hash()` and verify them with
  `PasswordHasher.verify()`.
- Put authentication-required endpoints behind `@auth.login_required`.
- Add `CSRFProtection` to browser applications that use cookie sessions.
- Configure exact CORS origins when credentials are allowed.
- Configure `TrustedHostMiddleware` in production.
- Do not trust `X-Forwarded-For` directly. Configure `ProxyHeadersMiddleware`
  with the direct proxy IP/CIDR after the proxy overwrites forwarded headers.
- Validate OAuth `state` before exchanging an authorization code.
- Use HTTPS and secure cookies in production.

## OpenAPI pattern

```python
from dataclasses import dataclass
from typing import Annotated

from higuma import Body, QueryParam


@dataclass
class CreateUser:
    name: str


@app.post(
    "/users",
    summary="Create user",
    tags=("users",),
    response_model=CreateUser,
    status_code=201,
)
def create_user(
    payload: Annotated[CreateUser, Body()],
    notify: Annotated[bool, QueryParam()] = False,
):
    return payload
```

Prefer real Python annotations and dataclasses. higuma resolves postponed
annotations and converts them to OpenAPI 3.1 schemas. Parameter markers enable
runtime input validation; `response_model` enables runtime output validation.
The legacy `request_body=` option is metadata-only.

## Verification checklist

Run all checks before presenting a change:

```bash
python -m ruff format --check python tests examples
python -m ruff check python tests examples
python -m pytest
python -m pytest tests/test_flask_helpers.py tests/test_flask_helpers_server.py
python -m pytest tests/test_gateways.py tests/test_gateways_server.py
cargo fmt --check
cargo clippy --locked --all-targets --all-features -- -D warnings
cargo test --locked --all-features
mkdocs build --strict
```

For a feature touching the network, also start a real server and test the
actual protocol. Test-client success alone is insufficient for WebSocket,
supervisor, streaming, and proxy changes.

## Repository map

- `src/lib.rs`: Rust HTTP, routing, WebSocket, SSR execution core
- `python/higuma/app.py`: public application API and dispatch
- `python/higuma/request.py`: request, multipart, uploaded files
- `python/higuma/parameters.py`: typed parameters, dependency injection, validation
- `python/higuma/response.py`: response types, streaming, SSE, cookies, files
- `python/higuma/background.py`: after-response tasks
- `python/higuma/views.py`: class-based views
- `python/higuma/database.py`: built-in SQLite ORM
- `python/higuma/auth.py`: sessions, login, OAuth
- `python/higuma/security.py`: password, token, CSRF, rate limit helpers
- `python/higuma/supervisor.py`: multi-process TCP supervisor
- `examples/`: runnable examples
- `docs/`: Japanese docs
- `docs/en/`: English docs
- `tests/`: behavior tests

## Compatibility expectations

- 0.11.1+ closes context-helper body streams/uploads after teardown (including on
  cancellation/unhandled errors in 0.11.2+). Keep reads within the context. Response
  background callables may be sync/async (including `response.background = fn`);
  streams close before background/DI cleanup and when replaced by after-request hooks.
  In 0.11.3+, `View`/`MethodView` also resolve typed parameters/DI and emit per-method
  OpenAPI schemas, and `Request.cookies` isolates malformed/reserved cookie pairs.
  In 0.11.4+, native `request.path` preserves trailing slashes (matching ASGI/WSGI/TestClient
  and fixing `mount_static(..., html=True)` directory indexes), duplicate request headers
  merge per RFC 9110, `make_response()` accepts `(name, value)` header sequences, native
  `app.run()` and WebSocket same-origin support IPv6, and `Observability` middleware
  records inner `HTTPException` statuses accurately.
  In 0.11.5+, `PasswordHasher` supports OWASP `scrypt` work factors (`n>=2**15`) up to a
  256 MiB cap, `Supervisor` binds IPv6 hosts, `StaticFiles` preserves `root_path` in `308`
  redirects, local dataclass/TypedDict hints are cached under `from __future__ import annotations`,
  and `Rule.build()` percent-encodes non-ASCII static route segments.
  In 0.11.6+, root mounts (`mount_wsgi("/")`, `mount_asgi("/")`, `static_url_path="/"`),
  empty body-allowed native `Content-Length: 0`, URL-encoded `MAX_FORM_PARTS`, case-insensitive
  `Range` / `StreamingResponse` header stripping, and lazy ORM `Field(default=...)` are fixed.
  In 0.11.7+, `CORSMiddleware`/`TrustedHostMiddleware`/`ProxyHeadersMiddleware` reject bare
  `str`/`bytes` sequence arguments, `TokenSigner`/`RateLimitMiddleware`/`generate_user_id()`/
  `HealthChecks` enforce finite non-bool bounds, `Database.close()` closes `:memory:` connections
  safely, native `add_route()` is atomic across methods, and `@app.cli.command()` handles
  postponed annotations in factory scopes.
  In 0.11.8+, `Response.set_cookie()` accepts `timedelta` for `max_age` and rejects `;`/CR/LF
  in cookie attributes, `EventSourceResponse` preserves duplicate headers, ASGI fallback 500
  responses preserve headers and suppress `HEAD` bodies, WebSocket same-origin checks canonicalize
  IPv6 literals and reject port `0`/empty `Host`, and `Supervisor`/`Higuma.run()` validate options
  and restore signal handlers cleanly on bind failure.
  In 0.11.9+, Rust and Python routing reject duplicate parameter names and oversized/non-finite
  numeric path segments cleanly as 404, `IntEnum` query/path parameters and RFC 3339 `z`
  datetimes convert cleanly, OpenAPI disambiguates colliding model names and emits exact
  `text/plain`/`application/octet-stream` media types for raw `Body()` inputs, and `StaticFiles`
  percent-encodes non-ASCII `308` redirects.
  In 0.11.10+, `LocalProxy` forwards attribute writes/deletes to the active request/app object
  (preventing cross-request attribute leaks on `request`), `AuthManager.login_user()` /
  `logout_user()` update `current_user` immediately in the same request, `OAuth2Client`
  validates `scopes`/`access_token` and supports preset `scopes` overrides, and
  `SessionMiddleware` / ORM `Boolean` & `Blob` enforce strict upfront validation.
  In 0.11.11+, native `HEAD` requests to unknown-length `StreamingResponse` routes omit
  `Content-Length` instead of synthesizing `0`, native WebSocket `101` handshakes forward safe
  preflight headers and bound post-`Close` teardown with a timeout, and `Observability`
  middleware attaches `X-Request-ID` to error responses too.
  In 0.11.12+, `ASGITestClient(root_path=...)` strips `root_path` on same-mount redirects and
  rejects redirects outside `root_path`, `Higuma`/`Blueprint` reject bare strings for
  `methods`/`tags`/`allowed_origins` and empty `methods=[]` while honoring `MethodView.methods`,
  `mount_wsgi()`/`mount_asgi()` validate atomically, `test_request_context()` applies
  `MAX_FORM_*`/`UPLOAD_MEMORY_LIMIT`, and `TestClient` escapes multipart parameters cleanly.
  In 0.11.13+, `AwaitBridge` resets cleared worker-thread `ContextVar` entries cleanly,
  `Signal` matches value-typed senders (`str`/`int`/etc.) by equality and synchronizes
  `connected_to()`, ASGI async fast path separates `@after_this_request` and finalizer error
  boundaries, `Response`/`RedirectResponse`/`StreamingResponse` preserve duplicate header pairs,
  `Request.close()` closes every upload and stream, `WebSocket` adds `asend()` and structured
  `send_json()`/`asend_json()`, and native `RequestBodyFile` supports `read(None)` and raises
  `ValueError` when closed.
  In 0.11.14+, `TrustedHostMiddleware` rejects port `0` and canonicalizes IPv6 hosts (`::1` /
  `[::1]`), `SecurityHeadersMiddleware` omits `X-Frame-Options`/`Referrer-Policy` when `None`,
  `ProxyHeadersMiddleware` matches IPv4-mapped IPv6 peers, `session |= {...}` works on the
  session proxy, `get_flashed_messages()` / `CSRFProtection` reject bare string iterables, and
  `HTTPException`/`ViteManifest`/SQLAlchemy extensions enforce strict upfront validation.
  In 0.11.15+, mounted WSGI `start_response(..., exc_info)` follows PEP 3333 before/after body
  emission, multipart parsing distinguishes malformed trailing delimiters (400) from excess parts
  (413), built-in ORM `Query.delete()`/`delete_all()` refuse `.limit()`/`.offset()` to prevent
  unbounded deletes, `JSONLogFormatter` serializes extras with `default=str` and includes
  `exc_info`, and `HealthChecks` recognizes async callable probe objects.
  In 0.11.16+, `serialize_json_value()` avoids dataclass `deepcopy()` and handles `Decimal`,
  `UUID`/`date`/`datetime` coercion requires `str`, numeric parameter bounds reject `bool`,
  and OpenAPI serializes `Literal[Enum]` values, preserves caller `responses`, omits `content`
  on `204`/`304`, and includes `PathParam`/`Body` descriptions.
  In 0.11.17+, `current_user` supports `==`/`hash`, auth decorators preserve `async def`
  functions and validate non-empty claim names, `OAuth2Client` validates HTTPS endpoints,
  HTTP(S) `redirect_uri`, and non-empty `code` upfront, and CLI `load_app()` supports dotted
  attribute paths while `build_parser()` catches nested blueprint command/group collisions.
  In 0.11.18+, route-declared `status_code` applies to `(body, headers)` 2-tuples, debug
  error pages format the passed exception's own traceback, `errorhandler`/`middleware`/
  `Blueprint` registration validates inputs upfront, WebSocket/ASGI enforces empty and
  duplicate `Origin` rejection plus text/close argument checks and safe concurrent disconnect
  teardown matching native mode, `TestClient`/`ASGITestClient` validate `base_url` ports and
  omit `Content-Length` on `HEAD` `204`/`205`/`304`, and native `HigumaCore.run(port=0)` logs
  the bound ephemeral port.
  In 0.11.19+, `g.pop(name)` without a default raises `KeyError` (matching Flask 3.1),
  `after_this_request()` validates callability upfront, `url_for()` validates required typed
  path parameters (`int`/`float`/`uuid`/`None`), `View.as_view()`/`MethodView` harden
  validation and `Allow` headers, `RedisSessionMiddleware` measures UTF-8 bytes for string
  Redis payloads, and `TokenSigner`/`RateLimitMiddleware`/`ViteManifest`/`Config`/`Request`/
  `Supervisor` enforce strict upfront parameter validation.
  In 0.11.20+, `TemplateResponse`/`render_template()` serializes dataclasses, enums, Decimals,
  UUIDs, and dates via `serialize_json_value()` into structured MiniJinja template contexts,
  `Response`/`make_response()` reject `bool`/non-`int` status codes and validate cookie
  attribute types, `wsgi_view()`/`asgi_view()` validate callability and protocol ordering, and
  `BodyBuffer`/`CommandGroup`/`CliRunner`/`Signal` enforce strict argument validation.
  In 0.11.21+, `Decimal` is supported end-to-end across parameter/model parsing, numeric
  constraints, and OpenAPI (`format: "decimal"`), OpenAPI normalizes integer `responses` keys
  and omits `content` on `205`, `AnonymousUser` implements `__eq__`/`__hash__`, `AuthManager`
  validates `user_loader` callability, and `HealthChecks`/`Observability`/`Model.__tablename__`
  validate inputs strictly upfront.
  In 0.11.22+, `Higuma` and `Blueprint` validate `import_name`, `lifespan`, route/WebSocket
  callables and explicit empty `endpoint=""`, all lifecycle/request/URL hook callables, and
  mount `name` arguments eagerly at registration time.
  In 0.11.23+, `FileResponse` rejects directories and non-regular files across both Python and
  native Rust, `RedirectResponse` validates non-empty string `location`, and `Headers` /
  `response.headers` follow `Mapping`/`dict` contracts for non-string keys and `.copy()`.
  In 0.11.24+, `Response`/`set_data`/`Request`/`receive_body`/`wsgi_view`/`asgi_view`/
  `TestClient`/`ASGITestWebSocket` reject non-bytes-like bodies (avoiding `bytes(int)` zero
  allocations), `set_cookie`/`delete_cookie` require string names/values, WSGI/ASGI/`TestClient`
  validate `FileResponse` runtime regular-file and range bounds before sending headers (matching
  native Rust), and the ASGI async fast path closes streams and runs background tasks if worker
  handoff fails before `respond()`.
  In 0.11.25+, `SessionMiddleware`, `TokenSigner`, and `PasswordHasher` enforce canonical
  unpadded base64url decoding (and canonical ASCII scrypt parameters) against token/signature
  malleability, multipart parsing rejects duplicate `Content-Transfer-Encoding`/`Content-Type`
  part headers with 400, and built-in security/CORS/host middleware constructors validate
  string/header arguments eagerly.
  Native disconnect finalization waits for pending iterator calls to return; it is not
  forced cancellation or durable task delivery.
- 0.11+ has synchronous HTTP URL preprocessors/defaults and Blueprint application-wide
  callbacks (once per app, even with aliases). Preprocessors run before before hooks
  and disable the ASGI async fast path. Use `g` for popped route values. Context-only
  helpers do not run preprocessing; `request.blueprints` lists child-to-parent names.
- Config file names are app-root-relative; `__` in prefixed environment keys means
  nested dictionaries. Config loaders are explicit, never request-controlled. Mapping
  and file loaders retain lowercase keys. No automatic dotenv/Python-file execution.
- `jsonify()`/`jsonify(None)` mean JSON null; use `jsonify({})` for an empty object.
  JSON `cache=False` bypasses parsed cache but keeps the already-buffered body.
- Preserve existing Flask-like route syntax.
- Add new public names to `python/higuma/__init__.py`.
- Keep runtime dependencies at zero unless a feature cannot reasonably be
  implemented with the standard library or Rust core.
- Update tests, Japanese docs, English docs, examples, changelog, and version
  together for a public feature.
- Treat response `Content-Type` as part of API correctness.
- Never set `Content-Length` manually; the Rust core derives it.
- Use `secure_filename()` for any user-visible upload name.
