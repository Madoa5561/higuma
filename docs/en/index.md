# higuma

**A Flask-inspired Python web framework backed by a Rust HTTP core.**

[日本語ドキュメント](../index.md){ .md-button }
[Get started](getting-started.md){ .md-button .md-button--primary }

```bash
pip install higuma
```

```python
from higuma import Higuma

app = Higuma(__name__)


@app.get("/")
def index():
    return {"message": "Hello from higuma"}


app.run()
```

## Highlights in 0.3.0

- Rust HTTP core powered by axum and Tokio
- Flask-like routing, blueprints, middleware, and hooks
- MiniJinja SSR with a compiled template cache
- WebSockets with auth preflight, origin checks, and bounded queues
- Multipart file uploads
- WSGI and ASGI application mounting
- Automatic OpenAPI 3.1 and Swagger UI
- Dependency-free built-in SQLite ORM
- Multi-process supervisor with bounded worker restarts
- Session authentication, password hashing, CSRF, and rate limiting
- OAuth 2.0 clients for Google, LINE, Discord, and custom providers
- Streaming file responses and automatic gzip
- Trusted proxy handling and strict response/session validation

## Continue

- [Feature guide](features.md)
- [Authentication and security](security-auth.md)
- [Examples](examples.md)
- [API reference](api.md)

!!! note
    higuma is under active development. Run load tests and a security review
    before adopting it for a production service.
