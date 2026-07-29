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

## Highlights in 0.2.0

- Rust HTTP core powered by axum and Tokio
- Flask-like routing, blueprints, middleware, and hooks
- MiniJinja SSR with a compiled template cache
- Text, binary, and JSON WebSockets
- Multipart file uploads
- WSGI and ASGI application mounting
- Automatic OpenAPI 3.1 and Swagger UI
- Dependency-free built-in SQLite ORM
- Cross-platform multi-process supervisor
- Session authentication, password hashing, CSRF, and rate limiting
- OAuth 2.0 clients for Google, LINE, Discord, and custom providers

## Continue

- [Feature guide](features.md)
- [Authentication and security](security-auth.md)
- [Examples](examples.md)
- [API reference](api.md)

!!! note
    higuma is under active development. Run load tests and a security review
    before adopting it for a production service.
