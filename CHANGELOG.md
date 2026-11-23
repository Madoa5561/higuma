# Changelog

All notable changes to higuma are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.11.25] - 2026-11-23

### Fixed

- Enforce canonical unpadded base64url decoding across `SessionMiddleware._load()`,
  `TokenSigner.loads()`, and `PasswordHasher.verify()` / `needs_rehash()` (`sessions.py`,
  `security.py`), rejecting non-base64 junk characters, explicit `"="` padding, extra `.`
  separators, and non-zero unused trailing padding bits (preventing token/signature/hash
  malleability). Also require strict canonical ASCII decimal parameters in `PasswordHasher`
  and validate `CSRFProtection(safe_methods=...)` elements eagerly.
- Reject multipart parts with duplicate `Content-Transfer-Encoding` or duplicate `Content-Type`
  headers with `400 BadRequest` (`multipart.py`), closing a bypass where only the first
  `Content-Transfer-Encoding` header was inspected.
- Validate middleware constructor options and decorator callability eagerly (`middleware.py`):
  require string elements in `_string_tuple()` (CORS, TrustedHost, ProxyHeaders), reject
  empty/control-character values in `CORSMiddleware` and `SecurityHeadersMiddleware`, reject
  empty patterns in `TrustedHostMiddleware`, and require a callable in `@middleware`.

## [0.11.24] - 2026-11-23

### Fixed

- Reject non-bytes-like values (such as `int`, `bool`, or `list[int]`) in `Response.__init__()`,
  `Response.set_data()`, `Request.__init__()`, `receive_body()`, `wsgi_view()`, `asgi_view()`,
  `TestClient` multipart uploads, and `ASGITestWebSocket.send_bytes()` instead of silently
  allocating zero-filled buffers via `bytes(int)`. Also require `str` cookie names/values in
  `Response.set_cookie()` / `delete_cookie()`, treat `None` ASGI request body chunks as `b""`,
  and require `str` in `ASGITestWebSocket.send_text()`.
- Align WSGI, ASGI (`_Body`), and `TestClient._materialize()` `FileResponse` handling with
  native Rust `file_response_from_py()`: verify regular-file status, `offset <= file_size`,
  and `length <= file_size - offset` before emitting headers (preventing truncated `200 OK`
  responses on shrunk files and computing `Content-Length` if the file appeared after
  `FileResponse` construction).
- Track handoff ownership in the ASGI async fast path (`async_dispatch.run()`) so
  `StreamingResponse._aclose()` and `response.background` still execute if worker handoff is
  cancelled or fails before `respond()` takes ownership.

## [0.11.23] - 2026-11-23

### Fixed

- Reject directories (`IsADirectoryError`) and non-regular files in `FileResponse.__init__()`
  (`response.py`) and reject non-regular file descriptors (`!metadata.is_file()`) in native
  Rust `file_response_from_py()` (`src/lib.rs`), preventing Linux from returning `200 OK`
  on an opened directory handle before failing mid-stream with `EISDIR`.
- Require a non-empty string `location` in `RedirectResponse.__init__()` (`response.py`).
- Enforce Python `Mapping`/`dict` contracts for non-string keys on `Headers` (`request.py`)
  and `_ResponseHeaders` (`response.py`) — returning `False`/default or raising `KeyError`
  instead of `AttributeError` — and preserve case-insensitive `_ResponseHeaders` on `.copy()`.

## [0.11.22] - 2026-11-23

### Fixed

- Harden `Higuma` and `Blueprint` constructor, route, hook, and mount registration validation
  (`app.py`, `blueprint.py`): require non-empty string `import_name` and callable `lifespan`
  in `Higuma.__init__()`; require callable handlers and non-empty string `endpoint` names
  (preserving explicit empty `endpoint=""` as an error instead of silently falling back to
  `__name__`) across `route()`, `add_url_rule()`, and `websocket()`; validate callability
  upfront in all `Higuma` and `Blueprint` lifecycle/request/URL hook decorators; and require
  non-empty string `name` in `mount_static()`, `mount_spa()`, `mount_wsgi()`, and
  `mount_asgi()`.

## [0.11.21] - 2026-11-23

### Fixed

- Add full `Decimal` input conversion and OpenAPI schema generation (`parameters.py`,
  `openapi.py`): parse finite `Decimal` values from `Decimal`, `int`, finite `float`, and
  numeric `str` inputs (rejecting `bool`, `NaN`, and `Infinity`) so `Decimal` works
  end-to-end across `QueryParam`/`Header`/`Cookie`/`Form`/`Body`, dataclass/TypedDict fields,
  and numeric bounds (`ge`/`gt`/`le`/`lt`), and map `Decimal` to
  `{"type": "string", "format": "decimal"}` in OpenAPI 3.1.
- Fix empty `tuple[()]` conversion on Python 3.10 and catch `(ValueError, TypeError)` in
  `Enum` conversion (`parameters.py`); normalize custom OpenAPI `responses` keys to strings
  so integer status keys never collide with auto-added `"422"`, and omit `"content"` on
  default `205` responses alongside `204`/`304` (`openapi.py`).
- Implement `__eq__` and `__hash__` on `AnonymousUser` so unauthenticated `current_user`
  equality/hashing is consistent, and validate `callable(user_loader)` upfront in
  `AuthManager.__init__()` and `AuthManager.load_user()` (`auth.py`).
- Harden `observability.py` and `database.py`: reject non-numeric `timeout` in
  `HealthChecks.__init__()` with `ValueError`, normalize bytes-like response header keys
  (`memoryview`) and `None` bodies safely in `Observability.wrap_asgi()`, and validate
  explicit `Model.__tablename__` identifiers upfront at class definition time.

## [0.11.20] - 2026-11-23

### Fixed

- Serialize `TemplateResponse.context_json` via `serialize_json_value()` (`response.py`) so
  dataclasses, enums, UUIDs, Decimals, dates, and paths render as structured MiniJinja
  template context values (e.g. `{{ card.name }}`) instead of Python `repr()` strings.
- Harden `Response` and `make_response()` validation (`response.py`): reject `bool` and
  non-`int` status codes consistently with `HTTPException`/`abort()` while continuing to
  accept `int` and `HTTPStatus`, and validate `samesite`, `path`, `domain`, and `expires`
  types in `Response.set_cookie()`.
- Harden mounted WSGI/ASGI adapters (`mounts.py`): require callable applications upfront in
  `wsgi_view()`/`asgi_view()`, reject non-empty WSGI chunks emitted before `start_response()`,
  reject ASGI `http.response.body` before `http.response.start` or duplicate start events,
  and accept `None`/`bytearray`/`memoryview` ASGI body chunks safely.
- Harden ingress, CLI, and signal validation: catch `(LookupError, ValueError)` for invalid
  multipart text charsets (`multipart.py`), reject negative/`bool`/non-`int` `length` in
  `BodyBuffer.fill_from()` (`body.py`), reject explicit empty `name=""` / non-string `name` /
  non-string `help` / non-callable handlers in `CommandGroup.command()` and `bytes` arguments
  in `CliRunner.invoke()` (`commands.py`), and require non-empty string names in `Signal()`
  (`signals.py`).

## [0.11.19] - 2026-11-23

### Fixed

- Match Flask 3.1 `g.pop()` semantics in `context.py` (raising `KeyError` when called without
  a default on a missing key while preserving `g.pop(name, default)`), and reject non-callable
  callbacks upfront in `after_this_request()`.
- Validate typed route parameter values in `Rule.build()` / `url_for()` (`routing.py`): reject
  `None` for required path parameters, validate `int` (rejecting `bool`/`float`/non-numeric
  strings and formatting `IntEnum` via `int()`), finite `float`, and `UUID` values instead of
  emitting malformed paths.
- Harden class-based views (`views.py`): validate `View.as_view()` string names, bare-string
  `methods`, and callable `decorators`, and include `HEAD` (when `GET` is allowed) and
  `OPTIONS` in `MethodView` `405 Method Not Allowed` `Allow` headers matching app routing.
- Harden upfront validation and edge cases across `security.py`, `redis_state.py`,
  `frontend.py`, `config.py`, `request.py`, `supervisor.py`, and `app.py`: validate
  `TokenSigner` `salt` & numeric `max_age`, `RateLimitMiddleware` / `RedisRateLimitMiddleware`
  numeric `window` & callable `key`, `csrf_token()` string `field_name`,
  `RedisSessionMiddleware` `store` type and UTF-8 byte length for `decode_responses=True`
  payloads, `ViteManifest.tags()` `nonce` type/control characters,
  `Config.from_prefixed_env()` callable `loads`, invalid `Request.text` `charset` raising
  `400 BadRequest`, and non-empty `host` / numeric timeouts in `Higuma.run()` and `Supervisor`.

## [0.11.18] - 2026-11-23

### Fixed

- Preserve route-declared `status_code` when a view returns a `(body, headers)` 2-tuple in
  `Higuma._coerce_view_result()` (both sync dispatch and ASGI async fast path), and format
  the passed exception's own traceback via `traceback.format_exception()` in debug mode
  instead of relying on ambient `sys.exc_info()`.
- Validate `Higuma` and `Blueprint` registration inputs upfront: reject invalid error handler
  keys (such as `bool`, out-of-range HTTP status codes, strings, or exception instances) and
  non-callable handlers in `errorhandler()`, `register_error_handler()`, and
  `app_errorhandler()`; reject non-callable middleware in `middleware()`/`add_middleware()`;
  and validate `Blueprint` instances and string names in `Blueprint.__init__()` and
  `register_blueprint()`.
- Align WebSocket `Origin`, text payload, and close validation across native and ASGI modes:
  reject empty or duplicate `Origin` headers (and duplicate `Host` headers) consistently in
  ASGI and `_dispatch_websocket_preflight()`, fall back to ASGI `scope["path"]` when
  `raw_path` is empty `b""`, reject non-`str` payloads in `WebSocket.send_text()` /
  `asend_text()`, reject `bool`/non-`int` close codes and non-`str` close reasons in
  ASGI WebSocket `aclose()`, and handle concurrent peer disconnect `OSError` cleanly during
  ASGI WebSocket `asend_*` / `aclose()` teardown (converting send disconnects to
  `WebSocketDisconnect(1006)` and ignoring redundant close-frame write failures).
- Fix `TestClient` and `ASGITestClient` edge cases: validate `base_url` ports upfront
  (rejecting port `0`, out-of-range, and non-numeric ports) and `root_path` type, omit
  `Content-Length` on `HEAD` responses with status `204`/`205`/`304`, and always close
  `StreamingResponse` in `TestClient._materialize()` when chunk iteration raises.
- Log the OS-assigned bound address (`listener.local_addr()`) in native `HigumaCore.run()`
  when binding with `port=0`.

## [0.11.17] - 2026-11-23

### Fixed

- Harden authentication & OAuth2 (`auth.py`): implement `__eq__` and `__hash__` on
  `_CurrentUserProxy` so `current_user == user` works properly; validate non-empty
  `session_key` and raise a clear `RuntimeError("AuthManager requires SessionMiddleware")`
  when `SessionMiddleware` is absent; preserve `async def` coroutine functions in
  `login_required`, `fresh_login_required`, `roles_required`, and `permissions_required`
  while rejecting empty/non-string claim names; validate HTTPS provider URLs
  (`authorize_url`, `token_url`, `userinfo_url`), HTTP(S) `redirect_uri`, and non-empty
  credentials upfront in `OAuth2Client.__init__()`; validate non-empty authorization `code`
  in `OAuth2Client.fetch_token()` before consuming the PKCE verifier.
- Improve CLI app loading and parser validation (`cli.py`): support dotted attribute paths
  in `load_app()` (such as `module:Holder.create()` or `module:container.app`) with
  identifier validation, and detect nested blueprint CLI command/group collisions cleanly
  with `ValueError` instead of internal `argparse.ArgumentError`.

## [0.11.16] - 2026-11-23

### Fixed

- Improve parameter serialization and validation (`parameters.py`): iterate dataclass
  `fields()` directly in `serialize_json_value()` instead of `dataclasses.asdict()`
  (eliminating unnecessary `copy.deepcopy()` calls and crashes on uncopyable fields),
  serialize `Decimal` values consistently across JSON/SSE/WebSocket outputs, require `str`
  inputs when parsing `UUID`/`date`/`datetime` values (preventing JSON integers such as
  `20260102` from coercing inconsistently across Python versions), and reject `bool` values
  in numeric constraint checks (`ge`/`gt`/`le`/`lt`).
- Improve OpenAPI 3.1 schema generation (`openapi.py`): serialize `Literal` and `Enum`
  values via `serialize_json_value()` (fixing `Literal[MyEnum.VALUE]` enum schemas), copy
  custom `responses` dicts before adding `"422"` so `app.openapi()` never mutates caller
  metadata, omit `"content"` on default `204`/`304` responses, and propagate
  `PathParam(description=..., deprecated=True)` and `Body(description=...)` metadata.
- Log the actual bound ephemeral port (`listener.getsockname()[1]`) in `Supervisor._serve()`
  when `port=0` is configured.

## [0.11.15] - 2026-11-23

### Fixed

- Fix PEP 3333 `start_response(status, headers, exc_info)` compliance in `wsgi_view()`
  (allowing error-recovery status/header replacement before any response bytes are written,
  re-raising `exc_info` after body emission begins, and rejecting repeated `start_response`
  calls without `exc_info`); harden `_mounted_path()` against encoded-slash prefix
  confusion and validate `1..65535` port bounds and empty `Host` fallback in
  `_server_address()`.
- Distinguish malformed trailing multipart delimiters (`400 Bad Request`) from excess parts
  (`413 RequestEntityTooLarge`) after `max_form_parts`.
- Prevent silent unbounded deletion in the built-in SQLite ORM by refusing `Query.delete()`
  and `Query.delete_all()` when `.limit()` or `.offset()` is set; validate `Model`
  instances/subclasses, non-string database URLs, and SQL identifiers cleanly; guard
  `Database.session()` rollback on closed connections so the original exception is never
  masked.
- Improve observability and static file validation: format non-JSON-native extras (`default=str`)
  and `exc_info` tracebacks in `JSONLogFormatter`, recognize async callable probe objects
  (`async def __call__`) in `HealthChecks`, normalize `bytes`-like ASGI headers in
  `Observability.wrap_asgi()`, and reject non-string `index_file` with `ValueError` in
  `StaticFiles`.

## [0.11.14] - 2026-11-23

### Fixed

- Harden `TrustedHostMiddleware`, `SecurityHeadersMiddleware`, and `ProxyHeadersMiddleware`:
  reject port `0` (`Host: example.test:0`) and canonicalize IPv6 literals (accepting both
  `::1` and bracketed `[::1]` in `allowed_hosts`, matching expanded spellings like
  `[0:0::1]:8000`); reject `bytearray` alongside `str`/`bytes` in middleware list helpers;
  omit `X-Frame-Options`/`Referrer-Policy` cleanly when `frame_options=None` or
  `referrer_policy=None` instead of emitting literal `"None"`; recognize IPv4-mapped IPv6
  dual-stack proxy peers (`::ffff:127.0.0.1`) against IPv4 `trusted_proxies`.
- Harden security utilities: reject bare `str`/`bytes`/`bytearray` for
  `CSRFProtection(safe_methods=...)` and non-string/empty `header_name`/`field_name`; return
  `ValueError("invalid token")` for non-string `TokenSigner.loads()` inputs; handle
  non-string inputs cleanly in `PasswordHasher.hash()`/`verify()`/`needs_rehash()` and
  `validate_user_id()`.
- Implement `_SessionProxy.__ior__` so `session |= {...}` works through the `session` proxy
  (matching `Session.__ior__`), reject bare `str`/`bytes`/`bytearray` in
  `get_flashed_messages(category_filter=...)`, reject boolean/non-int status codes in
  `HTTPException` / `abort()`, reject port `0` / non-str / whitespace / quote characters in
  `ViteManifest(base_url=...)`, and validate `SQLAlchemyExtension.init_app()` /
  `AsyncSQLAlchemyExtension.init_app()` database URLs upfront before importing optional
  dependencies.

## [0.11.13] - 2026-11-23

### Fixed

- Reset worker-thread-cleared `ContextVar` entries in `_AwaitChannel` (`AwaitBridge`) across
  subsequent `resolve()` calls without clobbering coroutine-owned `ContextVar` values.
- Fix `Signal` matching and disconnection for non-weakrefable value-typed senders (such as
  non-interned `str`/`int`/`tuple` values, without conflating `1` and `True`), reject
  callable objects with `async def __call__`, and synchronize `Signal.connected_to()` under
  the signal lock.
- Separate `@after_this_request` and `_higuma_response_finalizers` error boundaries in the
  ASGI async fast path (`async_dispatch.py`, matching `Higuma._dispatch()`), and ensure
  `StreamingResponse._close()` runs in the ASGI error fallback when `_Body()` fails before
  streaming starts.
- Accept iterable `(name, value)` pairs with duplicate headers (such as multiple
  `Set-Cookie` headers) across `Response`, `HTMLResponse`, `PlainTextResponse`,
  `JSONResponse`, `RedirectResponse`, `FileResponse`, `TemplateResponse`, and
  `StreamingResponse`, filtering case-insensitive `Location` / `Content-Length` cleanly.
- Make `Request.close()` close all uploaded files and the request body stream even if an
  earlier upload's `close()` raises; reject boolean/non-int `chunk_size` in
  `Request.iter_body()`/`aiter_body()` and boolean/non-int limits in `BodyBuffer`; fail fast
  before thread dispatch in `store_chunk()` on oversized bodies without masking cancellation.
- Reject non-bytes-like payloads in `WebSocket.send()`/`send_bytes()`/`asend()`/`asend_bytes()`
  (preventing `bytes(int)` from sending NUL bytes), add `WebSocket.asend()`, and serialize
  structured values (`dataclass`, `UUID`, `datetime`, `Enum`, `Decimal`) in
  `WebSocket.send_json()`/`asend_json()`.
- Reject non-bool defaults for boolean CLI flags in `CommandGroup`, catch malformed quote
  errors inside `CliRunner.invoke()`, and update native Rust `RequestBodyFile` to accept
  `read(None)` and raise `ValueError` on read/seek after `close()` (matching `BodyBuffer`
  and Python `io`).

## [0.11.12] - 2026-11-22

### Fixed

- Fix `ASGITestClient` redirect following when `root_path` is set: normalize `root_path`
  with a leading `/`, resolve relative redirects against the full `root_path`-prefixed
  request URL, strip `root_path` before dispatching same-mount follow-up requests (avoiding
  doubled `/prefix/prefix/...` 404s on `StaticFiles` `308` or `url_for()` redirects), reject
  redirects that escape `root_path` or use encoded-slash prefix ambiguity, and reject
  boolean/non-positive `max_redirects`.
- Harden `Higuma` and `Blueprint` route/app registration: reject boolean/non-int
  `max_content_length` and `request_memory_limit` in `Higuma.__init__()`; reject bare
  `str`/`bytes` for `methods`, `tags`, and `allowed_origins` and reject explicit empty
  `methods=[]` in `Higuma.route()`/`add_url_rule()`/`websocket()` and
  `Blueprint.route()`/`websocket()`; inherit `MethodView.methods` in `Higuma.route()` and
  `Blueprint.route()` when `methods=None`; make `mount_wsgi()` and `mount_asgi()` atomic on
  endpoint, route, or syntax conflicts.
- Copy `MAX_FORM_MEMORY_SIZE`, `MAX_FORM_PARTS`, and `UPLOAD_MEMORY_LIMIT` from `app.config`
  onto `Request` in `Higuma.test_request_context()`, normalize `_scheme` case-insensitively
  in `url_for(..., _external=True)`, and escape quotes/backslashes (while rejecting
  CR/LF/NUL) in `TestClient` multipart field names, filenames, and `content_type`.

## [0.11.11] - 2026-11-22

### Fixed

- Stop the native Rust server from synthesizing a misleading `Content-Length: 0` header on
  `HEAD` requests to unknown-length `StreamingResponse` routes (replacing nested
  `Router::fallback(any(dispatch))` with `Router::fallback(dispatch)` and preserving an
  unknown-length stream body marker until `axum`/`hyper` strips the `HEAD` body while still
  running background/cleanup tasks).
- Bound native WebSocket shutdown after receiving a client `Close` frame with
  `WEBSOCKET_CLOSE_TIMEOUT` so a half-open peer that omits TCP FIN cannot pin a Tokio task
  indefinitely, and forward safe preflight headers (such as `Set-Cookie`, `X-Request-ID`, and
  `Vary`, excluding hop-by-hop, framing, and `Sec-WebSocket-*` headers) onto native `101
  Switching Protocols` WebSocket responses (matching ASGI).
- Ensure `Observability` middleware attaches `X-Request-ID` even on inner-middleware
  `HTTPException` / error responses (matching `wrap_asgi()`), normalize lowercase ASGI HTTP
  methods in `wrap_asgi()`, reject boolean `max_series` in `Observability`, and reject
  non-string URLs cleanly with `ValueError` in `RedisExtension.init_app()`.

## [0.11.10] - 2026-11-22

### Fixed

- Forward `__setattr__`, `__delattr__`, `__bool__`, `__eq__`, and `__hash__` on `LocalProxy`
  (and `__setattr__`/`__delattr__` on `_CurrentUserProxy`) to the active context object so
  setting custom attributes on `request` (such as `request.tenant = ...`) stays isolated to
  the current request instead of mutating the global proxy singleton across requests.
- Synchronize `request.user` and `current_user` immediately inside `AuthManager.login_user()`
  and `AuthManager.logout_user()` so handlers see the updated authentication state within the
  same request without leaking context state.
- Reject bare `str`/`bytes` for `OAuth2Client(scopes=...)`, allow overriding `scopes` in
  `OAuth2Client.google()` / `.line()` / `.discord()`, reject empty or control-character
  `access_token` values in `OAuth2Client.userinfo()`, and omit `Content-Type:
  application/x-www-form-urlencoded` on bodyless `GET` OAuth requests.
- Validate `cookie_name`, `max_age` (positive non-bool `int`), and `samesite` upfront in
  `SessionMiddleware.__init__`, and reject boolean `max_age` in `RedisSessionMiddleware.__init__`.
- Close newly opened SQLite connections in `Database.connect()` if initial `PRAGMA` setup fails,
  reject float values (`0.0`/`1.0`) in ORM `Boolean` fields, require bytes-like inputs in ORM
  `Blob` fields (preventing integer-length NUL allocations), and reject empty prefixes in
  `Config.from_prefixed_env()`.

## [0.11.9] - 2026-11-22

### Fixed

- Prevent `500` crashes on oversized `<int:...>` URL segments (exceeding Python's integer
  string conversion limit) and reject non-finite overflowing `<float:...>` segments as `404`
  in both the Rust core and Python `Rule.match()`.
- Reject duplicate route parameter names (such as `/users/<int:id>/posts/<int:id>`) in both
  Python `Rule` and Rust `parse_route_pattern()`, and enforce Python identifier start rules
  (`[A-Za-z_][A-Za-z0-9_]*`) on route parameter names in Rust.
- Validate `Parameter` numeric/length bounds (rejecting booleans, non-finite numbers, and
  contradictory `ge`/`gt`/`le`/`lt` ranges), construct unwrapped optional unions safely on
  Python 3.10+ (`Optional[Annotated[Any, ...]]`), parse `IntEnum` string values while
  rejecting boolean coercion, support lowercase RFC 3339 `z` UTC suffixes without accepting
  bare `date` objects for `datetime`, and reject non-`None` values for `type(None)`.
- Disambiguate colliding OpenAPI schema names when distinct `dataclass`/`TypedDict`/ORM models
  share a class name, and emit `text/plain` / `application/octet-stream` requestBody content
  types for raw `str` / `bytes` `Body()` parameters.
- Percent-encode non-ASCII directory paths in `StaticFiles` `308` trailing-slash redirects,
  trim optional whitespace before `;` in `SPAFiles` `Accept` negotiation, reject `bytes` and
  non-string elements in `SPAFiles` prefixes, and return `400 Bad Request` on non-Latin-1
  multipart `Content-Type` headers.

## [0.11.8] - 2026-11-22

### Fixed

- Emit `Content-Type`/`Content-Length` headers and suppress body bytes for `HEAD` in the ASGI
  fallback `500` error path, close malformed ASGI WebSocket scopes with code `1002` instead of
  crashing, parse non-standard ASGI `spec_version` strings safely, and validate ASGI concurrency
  and WebSocket limits as positive non-bool integers.
- Harden WebSocket same-origin checks in both Python preflight and the Rust core to reject
  empty `Host` headers, port `0`, and malformed `Host` authority components, while canonicalizing
  equivalent IPv6 address literals (such as `[0:0:0:0:0:0:0:1]` vs `[::1]`).
- Support `datetime.timedelta` for `Response.set_cookie(max_age=...)` (matching Flask), reject
  boolean/non-integer `max_age` and attribute-injecting characters (`;`, CR/LF, NUL) in cookie
  `path`/`domain`/`expires`, ensure `StreamingResponse._close()`/`_aclose()` attempts every
  stream resource even if the first fails, and preserve duplicate/case-insensitive headers in
  `EventSourceResponse`.
- Restore `SIGINT`/`SIGTERM` handlers in `Supervisor._serve()` if socket binding fails, allocate
  distinct ephemeral worker ports simultaneously, validate `Supervisor` and `Higuma.run()`
  parameters before starting lifespan hooks, and strip whitespace around `If-Range` headers.
- Reject empty or malformed `module:object` references in `cli.load_app()`, raise on blueprint
  CLI group collisions with built-in or app commands in `cli.build_parser()`, require
  `start_response()` and exact prefix boundaries in mounted WSGI apps, and validate finite
  non-bool `timeout`/`max_redirects` in test clients while closing every active ASGI stream on
  exit.

## [0.11.7] - 2026-11-22

### Fixed

- Reject bare `str`/`bytes` arguments in `CORSMiddleware`, `TrustedHostMiddleware`, and
  `ProxyHeadersMiddleware` (preventing accidental character-by-character splitting of host,
  origin, or CIDR lists) and validate `CORSMiddleware(max_age=...)` is a non-negative non-bool
  integer.
- Validate `TokenSigner.loads(max_age=...)` and reject boolean `iat` timestamps, validate
  finite non-bool `limit`/`window`/`max_keys` and use `math.ceil()` for `Retry-After` in
  `RateLimitMiddleware`, and enforce ASCII prefix, `entropy_bytes >= 6`, and length bounds in
  `generate_user_id()`.
- Validate `HealthChecks(timeout=...)` and probe names, synchronize `HealthChecks` shutdown
  cleanup of pending probe futures and executor teardown under its internal lock, and reject
  repeated `RedisExtension` startup before shutdown to avoid leaking open client connections.
- Prevent `Database.session()` from leaking its in-memory SQLite lock if `connect()` raises,
  and add `Database.close()` to release `:memory:` connections explicitly.
- Make native `HigumaCore.add_route()` atomic across all methods so a duplicate or invalid
  method cannot leave partial registrations behind, and clamp `max_body_size` to at least 1 byte.
- Capture caller locals and resolve parameter annotations gracefully in `CommandGroup.command()`
  so factory-defined `@app.cli.command()` functions work under `from __future__ import annotations`
  even when return annotations reference local types.

## [0.11.6] - 2026-11-22

### Fixed

- Fix root-prefix route registration (`"/"`) in `mount_wsgi()`, `mount_asgi()`, and
  `Higuma(..., static_url_path="/")` so subpath rules normalize to `/<path:...>` instead
  of `//<path:...>`.
- Honor `STATIC_CACHE_MAX_AGE` on Blueprint static files, support PEP 420 namespace
  packages in `_find_root_path()`, parse HTTP `Range: bytes=...` units case-insensitively,
  and reject signed integer tokens (`+0`) in `_parse_range()`.
- Emit `Content-Length: 0` on empty body-allowed native responses (such as `200`/`201`/`202`
  responses with `BackgroundTasks`) instead of switching to chunked transfer encoding, and
  accept Python tuples of `(name, value)` pairs in native `extract_headers()`.
- Enforce `MAX_FORM_PARTS` on `application/x-www-form-urlencoded` requests in `Request.form`
  (matching `multipart/form-data`), skip form parsing for `HEAD` in `Request.values`, strip
  `Content-Length` case-insensitively in `StreamingResponse` when `content_length=None`, and
  percent-encode `/` in `FileResponse` RFC 5987 `filename*=UTF-8''...` headers.
- Avoid invoking callable `Field(default=...)` factories when explicit values are supplied
  to `Model(...)` or on attribute access, validate `String(length=...)` is a positive non-bool
  integer, preserve `limit(0)` in `Query.first()`, and refuse `Session.delete()` on unsaved
  instances whose primary key is `None`.

## [0.11.5] - 2026-11-22

### Fixed

- Compute and bound `maxmem` in `PasswordHasher` (`scrypt`, capped at 256 MiB) so
  OWASP-recommended work factors such as `n=2**15` and `n=2**16` succeed in `hash()`
  and `verify()` instead of failing OpenSSL's default 32 MiB limit, while still
  rejecting excessive-memory hashes.
- Cache resolved type hints on local `dataclass` and `TypedDict` models referenced by
  routes (including `request_body` and `response_model`) under
  `from __future__ import annotations` so factory-scoped models retain full validation
  and OpenAPI schemas; skip parameter constraints on `None` for optional constrained
  annotations; emit binary array schemas for `list[UploadFile]` `File()` inputs,
  1-element fixed `tuple[T]` schemas, and homogeneous `Enum` types in OpenAPI.
- Preserve `request.root_path` in `StaticFiles` directory-index `308` redirects when
  mounted behind ASGI `root_path` or WSGI `SCRIPT_NAME`, and deduplicate `Vary: Accept`
  case-insensitively in `SPAFiles`.
- Support IPv6 bind hosts (`::1`, `::`, `[::1]`) in `Supervisor` via `AF_INET6` and
  release connection slots cleanly if starting a proxy thread fails.
- Accept RFC 7578/2045 `Content-Transfer-Encoding: 7bit` and RFC 2231 `name*=`
  parameters in multipart form parsing.
- Prevent `ASGITestClient` redirect following from overwriting redirect target query
  strings with the original request's `query`/`query_string` or resetting custom
  origins, and handle negative `Max-Age`, past `Expires`, and bracketed cookie names
  in `TestClient` cookie tracking.
- Percent-encode non-ASCII static route segments in `Rule.build()` / `url_for()`,
  resolve nested dotted attributes in `Config.from_object()` while preserving inner
  import errors, skip empty `PREFIX_` keys in `Config.from_prefixed_env()`, support
  single-use iterables for `get_flashed_messages(category_filter=...)`, reject `bool`
  numeric limits in `RedisSessionStore`/`RedisRateLimitMiddleware`, repair missing TTLs
  in the Redis rate-limit Lua script, reset cleared `ServerSession` metadata, reject
  boolean `iat` in `SessionMiddleware._load()`, and clear `response.background` before
  execution in `TestClient` and the ASGI async fast path.

## [0.11.4] - 2026-11-22

### Fixed

- Preserve trailing slashes in native server `request.path` and WebSocket paths
  (while continuing to normalize paths for route matching), preventing an infinite
  `308` redirect loop on `mount_static(..., html=True)` directory indexes and keeping
  native behavior consistent with ASGI, WSGI, and `TestClient`.
- Support bare IPv6 bind hosts (e.g. `host="::1"`, `host="::"`) in `app.run()`, fix
  native IPv6 WebSocket same-origin validation (stripping brackets from `url::Url`
  hosts and rejecting malformed ports, non-`http(s)` schemes, userinfo, and duplicate
  `Origin`/`Host` headers), normalize trailing slashes in ASGI WebSocket
  `allowed_origins`, validate RFC 6455 close codes/reason length in native
  `WebSocketSession.close()`, and return a clean disconnect event when the receive
  channel closes.
- Merge duplicate request headers from `raw_headers` per RFC 9110 (`"; "` for
  `Cookie`, `", "` for all other headers) instead of overwriting earlier values,
  preserving multi-hop `X-Forwarded-For`, multi-header `Accept`, and rejecting
  bypass attempts via duplicate `Access-Control-Request-Headers`, `Origin`, or `Host`
  headers.
- Record inner middleware `HTTPException` responses (e.g. `429` from
  `RateLimitMiddleware` or `403` from `CSRFProtection`/`TrustedHostMiddleware`) with
  their actual status and `completed` outcome — and `asyncio.CancelledError` as
  `cancelled` — in `Observability.__call__`, and guard ASGI/mounted `Content-Length`
  and port parsing against malformed values.
- Support `(name, value)` header sequences in `make_response()` while preserving
  repeated headers such as multiple `Set-Cookie` entries, and split
  `ServerSentEvent.encode()` lines strictly on `\r\n`, `\r`, or `\n` per the WHATWG
  SSE specification.
- Rotate custom `CSRFProtection(field_name=...)` tokens on `AuthManager.login_user()`,
  avoid splitting single-string `roles`/`permissions` claims into individual
  characters in `roles_required`/`permissions_required` and WebSocket preflight auth
  checks, preserve existing query parameters in `OAuth2Client.authorization_url()`,
  and compare UTF-8 bytes in `OAuth2Client.validate_state()`.
- Close `FileResponse` handles if `seek()` fails in WSGI `_Body`, guard WSGI request
  storage cleanup and single-execution background handling on `OSError`, return
  standard headers and empty `HEAD` bodies on ASGI `400` path-encoding failures, and
  enforce `nullable=False` validation in `Database` `Session.save()` matching
  `Session.add()`.
- Upgrade the Rust `url` crate dependency to `>=2.5.9` to address `RUSTSEC-2026-0592`.

## [0.11.3] - 2026-11-22

### Fixed

- Wait for mounted ASGI applications in `mount_asgi` (`asgi_view`) to complete their
  HTTP response before delivering `http.disconnect` on `receive()`, and normalize
  percent-encoded/Unicode mount prefixes so mounted `Higuma.asgi_app` or ASGI sub-apps
  with `async def` routes are not prematurely cancelled.
- Parse `Request.cookies` per cookie pair with RFC 6265 unquoting so cookies containing
  brackets (e.g. `filter[page]=1`), names overlapping `Set-Cookie` attributes (`path`,
  `domain`, `version`, `secure`), or malformed sibling tokens do not discard valid
  cookies such as `higuma_session`.
- Run async `lifespan` context managers (`__aenter__`/`__aexit__`) and `on_startup`/
  `on_shutdown` hooks on the shared native async event loop so `ContextVar` tokens and
  loop-bound resources remain on the same task/loop across `with app.test_client():`
  and `app.run()`.
- Resolve `MethodView` (`get`, `post`, etc.) and `View.dispatch_request` arguments
  through `resolve_arguments` and inspect them in `generate_openapi` so class-based
  views support typed parameters (`QueryParam`, `Body`, `Depends`, `BackgroundTasks`)
  and per-method OpenAPI schemas.
- Handle optional `str | None` and `bytes | None` `Body()` annotations without routing
  text/binary payloads to JSON parsing, exclude `field(init=False)` dataclass fields
  from OpenAPI schemas, propagate `Parameter` constraints inside `Annotated[...]` in
  `schema_for`, and mark `= Body()` / `= Form()` / `= File()` defaults as `required`
  in OpenAPI `requestBody`.
- Normalize relative Blueprint `url_prefix` and route `rule` strings without leading
  slashes, support 0-argument `teardown_request` / `teardown_appcontext` hooks, avoid
  miscounting optional default arguments in hook signatures, validate CORS `OPTIONS`
  preflights before calling downstream handlers, deduplicate `Vary: Cookie`
  case-insensitively in `RedisSessionMiddleware`, and add `follow_redirects`, per-call
  `base_url`, case-insensitive stream headers, and string CLI invocation to test helpers.

## [0.11.2] - 2026-11-22

### Fixed

- Track `HEAD`, `Content-Length`, and bodyless (`204`/`205`/`304`) completion in
  `Observability.wrap_asgi` before the final ASGI body event so post-response
  disconnects are recorded as `completed` instead of `disconnected` or trace errors.
- Unwind event-loop cancellation before resolving worker futures in `AwaitBridge`,
  protect response/stream/WebSocket cleanup phases from late disconnect signals, and
  let ASGI WebSocket handlers await inside `WebSocketDisconnect` and teardown blocks.
- Wrap `response.background = callback` assignments in `BackgroundTask` with their
  captured context, preserve explicit `StreamingResponse(..., content_length=...)`
  across ASGI, WSGI, and `TestClient`, avoid draining streams on `HEAD` or bodyless
  statuses in `TestClient`, and close replaced or aborted `StreamingResponse` objects
  (including custom `Iterable`/`AsyncIterable` containers with `close`/`aclose`).
- Keep `request.stream` and `request.files` open until dependency cleanups and
  `teardown_request` finish on unhandled exceptions or cancellations, avoid creating
  a native `AwaitBridge` on purely synchronous requests, and run native cleanup on
  `TemplateResponse`/`FileResponse` errors, WebSocket preflight upgrades, bodyless
  responses (`204`/`205`/`304` without chunked framing), and WebSocket handler errors
  (closing with `1011`).
- Await `async def` `@auth.load_user` callbacks in `AuthManager` and treat loaded user
  objects without an explicit `is_authenticated` attribute as truthy in `current_user`.
- Update `rustix` to `1.1.6` in `Cargo.lock` (`RUSTSEC-2026-0486`).

## [0.11.1] - 2026-11-05

### Fixed

- Await raw async response background callbacks and retain their captured context,
  including callbacks assigned during response finalization.
- Close request-context-helper body streams and uploads after teardown, including
  exception paths; nested contexts sharing one Request close it only at outer exit.
- Explicitly close sync/async streaming iterators before background work and keep
  close idempotent across normal completion, HEAD, errors and early termination.
- Retain native response completion work when the transport drops a byte, file or
  streaming body. Keep pending Python iteration and its finalizer under one owner.
- Distinguish ASGI post-response disconnect notifications from mid-response
  disconnects, so successful response completion does not cancel background work.
  Account for HEAD and Content-Length completion before the final ASGI body event.

### Verification and boundaries

- Add unit and real native/Uvicorn/WSGI regression coverage and strengthen published
  wheel smoke tests for async background completion and request-body closure.
- Background work is still non-durable. Cleanup needs the process/runtime to remain
  alive; uncooperative blocking iterator calls cannot be forcibly stopped and may
  delay native disconnect cleanup until the call returns. No new dependencies.

## [0.11.0] - 2026-10-27

This release also includes the 0.9 and 0.10 development milestones below;
those versions were not separately published to PyPI.

### Added

- Flask-style application and nested Blueprint URL value preprocessors, URL defaults,
  direct URL-default injection and child-to-parent `request.blueprints` names.
- Blueprint application-wide before/after/teardown hooks, context processors,
  error handlers and URL callbacks, installed once per application despite aliases.
- Config file loader/binary mode/silent missing-path handling, app-root-relative
  filenames, dotted configuration classes, nested prefixed environment variables,
  custom environment decoders and namespace extraction. Export Config publicly.
- Dependency-free migration example, Flask comparison tests and actual native,
  ASGI and WSGI HTTP coverage. Japanese/English usage and migration boundaries.

### Fixed

- `jsonify()` and `jsonify(None)` produce JSON null, not an empty object; support
  multiple positional values and reject mixed positional/keyword values.
- Support parsed request JSON cache bypass, empty `make_response()` calls and
  `(body, header_mapping)` response tuples. Preserve Higuma response options.
- `Config.from_envvar(..., silent=True)` also handles a missing target file.

### Migration boundaries

- Use `jsonify({})` explicitly when an empty JSON object is intended.
- Config file paths are now app-root-relative; pass absolute paths for old cwd-based
  calls. Environment names containing `__` now describe nested dictionaries.
- Mapping/file config retains lowercase keys, unlike Flask. No Python-file execution,
  dotenv autoloading or instance-folder API. Request JSON cache bypass retains the
  buffered body, unlike consuming an uncached Werkzeug input stream.
- URL callbacks are synchronous HTTP hooks; ASGI routes with URL preprocessors use
  the compatibility bridge. Flask extension, routing and response-class compatibility
  remains partial. No new runtime dependencies.

## [0.10.0] - 2026-10-27

### Added

- Conservative ASGI async HTTP fast path with an explicit compatibility opt-out;
  preserve synchronous middleware/hooks/DI via automatic bridge fallback.
- ASGI-loop-owned AsyncSQLAlchemyExtension, explicit transactions, rollback on
  cancellation, isolated dependency sessions and shutdown disposal.
- Dependency-free app-scoped lifecycle Signals, weak receiver/sender references,
  temporary subscriptions and successful-render notifications. Not Blinker interoperability.
- Optional Redis server sessions with signed opaque cookies, TTL, size limits,
  rotation, revocation and CAS against stale writes. AuthManager accepts a session middleware class.
- Atomic Redis fixed-window rate limits shared across processes; state outages fail closed.
- Bounded local metrics, structured secret-minimizing records, request IDs,
  optional explicit OpenTelemetry instrumentation and liveness/readiness probes.
- Async API/SQLite, CSRF-protected Redis session and Signals examples, Japanese/
  English operations documentation, real communication and cancellation tests.

### Fixed

- Session proxy clear delegates to the underlying session implementation, preserving
  custom server-session rotation behavior.
- Audit target runtime dependencies separately from patched Python 3.14 tooling;
  do not install unpatched Python 3.10-only audit-tool dependencies or ignore advisories.

### Boundaries

- Fast-path eligibility is intentionally narrow; input is still received before dispatch.
- Async DB adapters require an owning ASGI loop. DB migrations/drivers are external.
- Redis sessions do not retroactively cancel in-flight authorization; quotas use
  fixed windows and need storage capacity planning. CSRF/TLS/ACL remain explicit.
- No automatic telemetry exports or public metrics endpoint. Probe timeouts cannot
  forcibly stop uncooperative blocking code. Metrics are process-local.

## [0.9.0] - 2026-10-27

### Added

- Nested Blueprint registration, aliases, inherited hooks/error handlers/context
  processors, and per-component template/static directories. Validate route/name
  collisions and cycles before registering the tree.
- Argparse-backed application and Blueprint CLI commands with scalar typed
  arguments, app contexts, async callbacks, a shell, and a CLI test runner.
  This is not Flask/Click extension compatibility.
- Dependency-free `ASGITestClient` / `app.test_async_client()` for asyncio HTTP,
  lifespan state, cookies, bounded response streams, disconnects, and WebSockets.
- Optional SQLAlchemy 2.x and Redis integrations with app-isolated resources,
  explicit transactions, dependency cleanup, and lifecycle-managed connections.
- A factory-based login/admin/JSON API example with nested components, CSRF,
  password hashing, role checks, SQLAlchemy, and private password-entry CLI.
- Bounded request-body spooling in native, ASGI, and WSGI transports; rewindable
  `request.stream`, `iter_body`/`aiter_body`, body size/spooling metadata, incremental
  multipart parsing, per-field/part/upload memory limits, and automatic cleanup.
- Reproducible real-socket comparison of native/ASGI/WSGI Higuma, Flask/Waitress,
  and Starlette/Uvicorn, including correctness, errors, latency, CPU and RSS data.
- Reuse a process-local auxiliary loop for ordinary native/WSGI async HTTP work
  instead of creating Windows loop-control sockets per await. Preserve request
  task context and close request await scopes, including failed test-client streams.

### Boundaries

- Shutdown hooks also clean up partially completed startup; all cleanup hooks are
  attempted even if one fails. Hooks must tolerate resources that never initialized.
- Upload spooling finishes receiving before dispatch; stored-body chunk reading
  is not a live network-body iterator. JSON/body convenience access can still
  materialize the full body. Temp storage needs disk space and operational limits.
- SQLAlchemy migrations and durable jobs remain external. Async Redis clients
  require the shared ASGI event loop; use synchronous clients for native/WSGI.
- Benchmark results describe a recorded local environment, not universal speed
  rankings or production-capacity guarantees.

## [0.8.0] - 2026-10-25

### Added

- Explicit `app.asgi_app` and `app.wsgi_app` exports alongside the built-in Rust
  server. ASGI 3/asyncio handles HTTP, WebSocket, and lifespan; WSGI handles HTTP.
- Same-server-loop async lifespan, handlers, dependencies, streams, and background
  work, with persistent await contexts for generator dependency cleanup.
- Dedicated bounded ASGI coordination workers, disconnect cancellation,
  chunk-level response backpressure, and bounded WS messages/receive queues.
- Awaitable WebSocket send/receive/close helpers for ASGI and native deployment.
- Optional `asgi` server extra, three-mode runnable example, protocol/real-server
  tests, and comprehensive Japanese/English deployment boundary documentation.

### Fixed

- Preserve root prefixes through URL generation, Swagger/OpenAPI, and nested
  Flask/ASGI mounts; expose decoded `request.root_path` and optional ASGI `scope`.
- Parse split Cookie headers without dropping values, preserve separate response
  Set-Cookie fields, normalize response framing, and close streamed resources.
- Avoid reverse-DNS-dependent startup in the loopback-only WSGI demonstration.

### Boundaries

- ASGI still uses coordination workers for sync API compatibility, not a purely
  coroutine-only pipeline. WSGI has no standard WS/lifespan protocol.
- Request bodies and third-party application mounts remain buffered. External
  servers own transport/compression/workers; Rust-native performance is not implied.
- Higuma/Flask/Starlette internal extension APIs are not made interchangeable by
  implementing standard server interfaces.

## [0.7.1] - 2026-10-25

### Security

- Raise the Tokio dependency floor and locked version to 1.53.3, addressing the
  upstream I/O-safety warning RUSTSEC-2026-0386. Higuma does not directly use the
  affected Unix `AsyncFd` constructors; no exploitable Higuma path was confirmed.
- Fail Rust dependency audits on `unsound` warnings, rather than allowing those
  informational findings to pass the release gate unnoticed.

### Release

- Ship the Flask-style compatibility, lifecycle, docs, and examples listed under
  0.7.0 below. Its publication was cancelled before PyPI/GitHub Release creation;
  the existing source tag is retained without rewriting it. Upgrade from 0.6.0
  directly to 0.7.1.

## [0.7.0] - 2026-10-24

Publication cancelled; changes are included in 0.7.1. No 0.7.0 PyPI distributions
or GitHub Release were published.

### Added

- Flask-style request contexts, request teardown, and request-local
  `after_this_request` callbacks, with sync/async callback support.
- Blueprint-scoped before/after/teardown hooks, error handlers, and template
  context processors; explicit template values retain priority.
- Query-first `request.values`, MIME/URL helpers, buffered response data helpers,
  and test-client `query_string`, `base_url`, and `content_type` arguments.
- Real Flask parity tests, real-HTTP lifecycle/CSRF/session regressions, a runnable
  lifecycle example, and Japanese/English testing and compatibility guides.

### Fixed

- Save signed sessions and apply `Vary: Cookie` after all response hooks, including
  response replacements and handled error responses.
- Parse JSON bytes without replacing invalid Unicode; support UTF-8/16/32 and
  recognize only application JSON MIME types. Silent failures do not poison reads.
- Return 500 if an HTTP error handler fails, preserve the actual failure for
  teardown, and restore request/app contexts even when cleanup raises.
- Resolve relative test-client redirects against the current URL, preserve HTTPS
  and 307/308 request bodies, clear entity headers on GET redirects, and reject
  external hosts.

### Changed

- Response `get_json()`/`.json` is now MIME-aware and returns `None` for non-JSON
  responses; use `get_json(force=True)` when parsing those bodies intentionally.
- Reject empty/dotted Blueprint names, dotted local endpoints, and duplicate
  registered Blueprint names to keep scoped dispatch unambiguous.
- Existing duplicate-key MultiDict last-value behavior and origin-root
  `request.base_url` are preserved; differences from Flask are documented.

## [0.6.0] - 2026-10-23

### Added

- Native SPA/CSR mounts with subpath support, guarded history fallback, explicit
  API/asset exclusions, and preservation of known HTTP method contracts.
- Multiple static mounts, optional HTML directory indexes, safe path resolution,
  stable web MIME types, cache controls, and dotfile/symlink escape protection.
- Vite manifest snapshots with hashed asset URLs, recursive CSS/modulepreload
  resolution, cycle handling, escaped HTML tags, CSP nonces, and explicit reloads.
- Dependency-free CSR browser example, Vite/Python HTML example, and comprehensive
  Japanese/English frontend guides and native/integration/external capability tables.
- Real-socket SPA/API/WebSocket coexistence and conditional file regression tests.
- An imagegen-created Japanese brown bear logo, shared documentation branding,
  and a reorganized README with release boundaries and project navigation.

### Fixed

- Parse quoted ETag lists without splitting commas inside entity tags; reject
  malformed lists instead of matching an accidental fragment in file preconditions.
- Generate an English sitemap instead of a 404 and resolve language selector links
  at build time so switching languages preserves the corresponding page.

### Changed

- Share file conditional handling across built-in static, new mounts, and complete
  FileResponse/send_file responses: ETag lists/weak matching, Last-Modified,
  If-Modified-Since, If-Match, If-Unmodified-Since, and strong/date If-Range.
- Built-in static serving now denies hidden files and uses the same safe path
  policy as new mounts. HTML/service-worker filenames default to revalidation.

## [0.5.0] - 2026-10-19

### Added

- Flask-style application contexts, isolated `g`, session proxy, flash messages,
  context checks, `app.extensions`, teardown callbacks, and direct error registration.
- URL generation with blueprint-relative endpoints, repeated query values,
  fragments, and explicit external URL/scheme options; request endpoint metadata.
- Real Flask integration tests and optional `compat` extra, executable Flask
  migration/context examples, and Japanese/English compatibility documentation.
- Scheduled Python/Rust dependency audits and a release gate requiring the latest
  CI and dependency-audit runs for the exact release commit to succeed.

### Fixed

- Preserve Secure/HttpOnly/SameSite when deleting session cookies, including
  browser-enforced secure cookie prefixes.
- Keep active rate-limit histories under identity churn and overflow promotion,
  while bounding stored identities and request histories.
- Compare effective WebSocket Origin scheme after trusted proxy processing;
  retain explicit origin allowlists and non-browser clients without Origin.
- Decode mounted WSGI/ASGI Unicode paths according to their protocols and drop
  underscore-named WSGI request headers to prevent normalization collisions.
- Make response header access case-insensitive and validate in-place header unions.
- Apply template context processors through the top-level helper without consuming
  existing template variables named `status` or `headers`.
- Align Python numeric path matching with the Rust core's ASCII converters.
- Accept no-argument `module:create_app()` CLI factory syntax without expression evaluation.
- Require passwords and CSRF in the token/roles example and remove its unsupported
  login argument. Integrate Dependabot PR #12's Rust dependency lock updates.

## [0.4.1] - 2026-10-19

### Fixed

- Add Vary: Cookie to session middleware responses to isolate cached user content.
- Reject excessive JSON nesting and parser integer-limit failures as HTTP 400,
  honoring silent parsing without caching rejected input.

- Revalidate dataclass response instances and filter subclass fields recursively,
  including models nested in collections, unions, and TypedDicts.
- Apply Annotated constraints to nested input and output values.
- Report float conversion overflow as a validation error instead of HTTP 500.

### Security

- Reject OAuth token/userinfo redirects to prevent credential forwarding.
- Reject implicit signing-key conversion from integers or other non-string/bytes values.
- Return HTTP 403 for non-ASCII CSRF tokens instead of an unhandled exception.
- Reject missing or malformed Host authorities in TrustedHostMiddleware.

## [0.4.0] - 2026-08-27

### Added

- Runtime-validated `Annotated` inputs for query, header, cookie, path, JSON,
  form, and uploaded-file values with structured HTTP 422 errors
- Nested sync, async, and yield dependencies with request caching and test
  overrides through `Depends` and `app.dependency_overrides`
- Runtime `response_model` validation, conversion, field filtering, and route
  `status_code` declarations
- Sync and async `StreamingResponse`, `EventSourceResponse` / `ServerSentEvent`,
  and ordered after-response background tasks
- Sync and async application lifespan context managers with `app.state` and
  lifecycle-aware test-client context managers
- Flask-style `View` and `MethodView` class-based routes
- Modern request URLs, redirect following/history, partitioned-cookie support
  on Python 3.14+, and JSON serialization for standard typed values
- Real-socket HTTP, WebSocket, streaming, SSE, background-task, file-range, and
  framing regression suites
- Comprehensive Japanese and English API reference plus typed API, streaming,
  SSE, lifespan, background-task, and class-based-view examples

### Changed

- Static and direct file byte ranges are streamed from Rust with bounded seek/read instead of
  loading the selected range into Python memory
- Streaming responses preserve their captured request context through lazy
  iteration and skip gzip for SSE and partial-content responses
- OpenAPI 3.1 now derives parameters, request bodies, validation responses, and
  constrained schemas from typed inputs and dependencies
- CI covers CPython 3.10 through 3.14 and Windows, Linux, Intel macOS, Apple
  Silicon, and Linux ARM64 release artifacts
- Rust dependencies were refreshed to maintained axum, Tokio, PyO3, MiniJinja,
  tower-http, futures-util, and serde_json releases

### Fixed

- HEAD and bodyless status framing, duplicate response headers, hop-by-hop
  header filtering, final informational responses, and Rust/Python path
  converter parity
- WebSocket preflight status handling, bounded handler/task queues, close/error
  codes, and reader/writer task shutdown
- Session modification tracking, custom CSRF fields, response status mutation,
  ORM default/type/numeric validation, and async request-context isolation
- Test-client redirect method/body semantics and streamed/file response reading

### Security

- OAuth state, PKCE verifier, and OIDC nonce values are session-bound per
  authorization request, bounded for parallel login tabs, single-use on
  success, and protected from caller parameter replacement
- Authenticated OAuth token and userinfo requests share the configured HTTP
  session, limits, and headers
- Password hasher parameters and encoded salt lengths are strictly validated
- Validation errors redact cookie, password, token, secret, authorization, and
  API-key inputs
- `secure_filename()` produces a safe fallback for empty and traversal-only
  names
- Release automation verifies version/tag alignment, smoke-tests every built
  artifact, and publishes GitHub releases only after successful PyPI upload

## [0.3.0] - 2026-07-29

### Added

- Trusted `ProxyHeadersMiddleware` for forwarded client IP and scheme
- Same-origin WebSocket checks with per-route `allowed_origins`
- WebSocket routes on `Blueprint`
- Rust streaming for `FileResponse` and automatic gzip response compression
- ORM `offset()` pagination and explicit `delete_all()`
- Public `secure_filename()` helper
- Automatic worker restart limits in the multi-process supervisor
- Bounded supervisor connections and bounded rate-limit identity storage
- Recursive dataclass, enum, and literal OpenAPI schemas

### Changed

- Python handlers run on Tokio's blocking pool instead of runtime worker threads
- WebSocket queues are bounded and messages use the request body size limit
- WebSocket authentication is checked before the HTTP 101 upgrade
- Browser-session and persistent remember-login cookies now behave differently
- Signed-session and token secrets must contain at least 32 bytes
- `Query.delete()` refuses an unfiltered mass delete; use `delete_all()` explicitly
- Route matching now rejects encoded slashes, invalid UTF-8, controls, and ambiguous paths
- Static invalid byte ranges return HTTP 416

### Security

- Automatic CORS preflight now passes through Python middleware
- Unexpected Python, template, and after-hook details are hidden from HTTP clients
- Swagger UI configuration is escaped and its CDN version is pinned
- OAuth provider responses are limited to 1 MiB and OIDC nonce is included
- Session cookies are limited to 4093 bytes and rotate on login/logout
- HTTP status codes and response headers are validated before reaching Rust
- Response headers remain validated when middleware mutates them
- WSGI and ASGI mounts preserve duplicate incoming headers
- Large files no longer allocate their full size in server memory
- Forwarded headers are ignored unless the direct peer is trusted
- CI enforces Clippy and Ruff; Dependabot monitors all dependency ecosystems

## [0.2.0] - 2026-07-29

### Added

- Rust-backed text, binary, and JSON WebSockets
- Multipart form parsing and `UploadFile`
- WSGI and ASGI application mounts
- Automatic OpenAPI 3.1 schemas and Swagger UI
- Shared MiniJinja compiled template cache
- Built-in SQLite ORM with declarative models and transactions
- Cross-platform multi-process TCP supervisor
- Session authentication and `login_required`
- Fresh-login, role, and permission authorization decorators
- scrypt password hashing and signed expiring tokens
- CSRF protection, rate limiting, and user ID helpers
- OAuth 2.0 clients for Google, LINE, Discord, and custom providers
- Japanese and English documentation
- Expanded runnable examples and LLM-oriented `SKILLS.md`

### Security

- Signed cookie sessions now enforce their configured maximum age
- Rate limiting uses the peer address supplied by the Rust server
- OAuth state values are signed and time-limited
- OAuth state is session-bound and single-use with automatic PKCE S256
- Untrusted token, cookie, salt, and digest sizes are bounded before decoding

## [0.1.0] - 2026-07-27

### Added

- Rust HTTP core based on axum and Tokio
- Flask-inspired route decorators and dynamic route converters
- Request and response objects, JSON, redirects, files, and SSR
- Automatic HEAD and OPTIONS behavior
- Error handlers, request hooks, middleware, and lifecycle hooks
- Blueprint route groups and URL generation
- Static files with ETag, conditional requests, and byte ranges
- Signed cookie sessions
- CORS, security header, and trusted host middleware
- Async Python handler compatibility
- In-process test client
- CLI for running applications and listing routes
- GitHub Actions CI and release wheel workflow
- PyPI Trusted Publishing with multi-platform wheels
