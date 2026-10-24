<div class="brand-hero" markdown="1">

![higuma — a Japanese brown bear](../assets/higuma-mark.png){ .brand-hero__mark width="144" height="132" }

# higuma

**Write in Python, serve with Rust.**

[日本語ドキュメント](../index.md){ .md-button }
[Get started](getting-started.md){ .md-button .md-button--primary }

</div>

Build APIs, HTML pages, and realtime connections with familiar Python decorators.
The name higuma comes from the Japanese brown bear.

!!! info "New in 0.8.0"
    Deploy through `app.asgi_app` or `app.wsgi_app` as well as the built-in server.
    ASGI supports HTTP, lifespan, and WebSockets; WSGI supports HTTP.
    See [Server interfaces](server-interfaces.md) for worker, streaming, proxy,
    and resource boundaries, and [Testing](testing.md) for verification.

```bash
python -m pip install -U higuma
```

```python
from higuma import Higuma

app = Higuma(__name__)


@app.get("/")
def index():
    return {"message": "Hello from higuma"}


app.run()
```

## Design

higuma combines a Flask-inspired decorator API with a built-in HTTP server
powered by axum and Tokio. Application logic stays in Python; the Rust core
handles sockets, route matching, file streaming, gzip, and WebSockets.

higuma is not a drop-in Flask replacement. External servers use the explicit
`asgi_app` / `wsgi_app` entry points; hosting other applications through buffered
HTTP mounts remains available in the opposite direction.

## Core features

- `Annotated` typed inputs, dependency injection, response models, and structured 422s
- Typed converters, automatic HEAD/OPTIONS, sync/async handlers, and class-based views
- Blueprints, middleware, request/response hooks, and lifespan
- MiniJinja SSR, context processors, and a compiled template cache
- Sync/async streaming, SSE, background tasks, range/ETag files, and automatic gzip
- Text, binary, and JSON WebSockets with same-origin and auth preflight checks
- Multipart uploads and a safe filename helper
- OpenAPI 3.1 / Swagger UI schemas for dataclasses, TypedDicts, enums, and more
- SQLite ORM with transactions and pagination
- Signed sessions, login, roles/permissions, and OAuth 2.0 with PKCE
- Flask-style application contexts, `g`, session proxies, flash messages, and URL generation
- Password, token, CSRF, CORS, rate-limit, trusted-host, and proxy helpers
- In-process testing, CLI commands, and a multi-process supervisor
- SPA/CSR history fallback, multiple static roots, and Vite manifests (0.6.0+)

!!! important "OpenAPI and input validation"
    Markers such as `Annotated[..., Body()]` validate and convert runtime input
    and generate OpenAPI from the same declaration. `response_model` validates
    output. The legacy `request_body=` option and a return annotation by itself
    remain schema metadata.

## Supported scope

- CPython 3.10 or newer
- ABI3 wheels for Windows, Linux, and macOS
- The built-in ORM supports SQLite only
- A reverse proxy is recommended for TLS, buffering, and network-level limits

## Continue

- [Getting started](getting-started.md)
- [Feature guide](features.md)
- [CSR and frontend](frontend.md)
- [Capability matrix](capabilities.md)
- [Flask compatibility](flask-compat.md)
- [ASGI, WSGI, and servers](server-interfaces.md)
- [Testing and request contexts](testing.md)
- [Authentication and security](security-auth.md)
- [Examples](examples.md)
- [API reference](api.md)
- [Deployment](deployment.md)
- [Brand assets](branding.md)

!!! warning
    higuma is under active development. Run workload-specific load, failure,
    and security tests before adopting it in production.
