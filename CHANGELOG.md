# Changelog

All notable changes to higuma are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
