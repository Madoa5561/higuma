# Capability matrix and architectural boundaries

Higuma combines a Rust HTTP core with Python APIs. It does not claim to embed every
feature found in every framework. This matrix distinguishes **native**, **integration**,
and **external infrastructure**. Features added in 0.6.0 are marked in the table.

| Area | Delivery | Scope / documentation |
| --- | --- | --- |
| Routing / Blueprint / class views | Native | Typed paths, HTTP methods, hooks, MethodView. [Features](features.md) |
| Validation / DI / OpenAPI | Native | Annotated, dataclasses, TypedDicts, yield cleanup, output filtering. [API](api.md) |
| SSR | Native | MiniJinja HTML; not complete Jinja2/Python callable compatibility |
| CSR / SPA / history routing | Native (0.6) | Safe fallback, reserved APIs, subpaths. [Frontend](frontend.md) |
| Vite / React / Vue / Svelte | Integration | Build output and manifest CSS/preloads; compilers, Node, HMR stay external |
| Static generation / MPA | Integration (0.6) | Serve externally generated directory indexes; no built-in generator |
| Static / HTTP cache / downloads | Native | Multiple roots, validators, ranges, Rust streaming. [Frontend](frontend.md) |
| Compression | Native + infrastructure | Rust gzip; Brotli selection/CDN invalidation at proxy/CDN |
| WebSocket / SSE / streaming | Native | Native endpoints, bounded queues, Origin, sync/async streams. [Features](features.md) |
| Session / login / roles / permissions | Native | Signed cookies, fresh login, password hashing. [Security](security-auth.md) |
| OAuth | Native + external services | Google/LINE/Discord helpers; provider configuration and production E2E are application responsibilities |
| CORS / CSRF / Host / proxy / rate limits | Native | Explicit trust boundaries; rate limits are process-local, not distributed |
| SQLite ORM / transactions | Native | Fields, queries, commit/rollback; migrations, relationships, pools, other databases need specialized libraries |
| Background tasks | Native | Lightweight after-response work; durable queues, retries, schedulers are external |
| Lifespan / supervisor | Native | Sync/async lifecycle and multiple processes. [Deployment](deployment.md) |
| Flask compatibility | Partial + WSGI integration | g/session/flash, request contexts and scoped Blueprint hooks (0.7). [Boundaries](flask-compat.md); not all extensions |
| WSGI / ASGI applications | HTTP integration | Buffered HTTP only, not mounted WS, ASGI lifespan, or indefinite streams |
| GraphQL / RPC | External library integration | Mount compatible HTTP apps; subscription forwarding is not guaranteed |
| Application caches / Redis / durable jobs | External | Manage clients through lifespan/DI; distinct from HTTP caching |
| i18n / mail / object storage / payments | External | Call libraries/services from views/DI; keep secrets server-side |
| PWA / offline / service workers | Frontend | Serve public files; generation and update/offline strategies are separate |
| Logs / metrics / tracing | Logging + middleware | Hooks/Server-Timing; monitoring backends and exporters are external |
| TLS / HTTP2/3 / CDN / edge WAF | Infrastructure | Terminate at a trusted reverse proxy in front of Higuma |
| Tests | Native + repository suite | Client, request contexts (0.7), Flask parity tests, real HTTP/WS, wheel smoke. [Testing](testing.md); browsers are separate |

## Extension principles

Keep zero Python runtime dependencies, Rust-side streaming, and explicit security
boundaries. Do not label thin stubs as durable queues or database migrations.
Verify buffered HTTP, event-loop, and process-local constraints before integrating
external libraries. External does not mean impossible: select the appropriate
routing, middleware, lifespan, DI, or mount boundary and verify the complete flow.
