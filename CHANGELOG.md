# Changelog

All notable changes to higuma are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
