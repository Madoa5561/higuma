# Changelog

All notable changes to higuma are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
