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
| HTTP route | `@app.get`, `post`, `put`, `patch`, `delete` |
| Realtime connection | `@app.websocket("/ws")` |
| HTML SSR | `app.render_template("page.html", value=value)` |
| JSON | return `dict`/`list` or call `app.jsonify(...)` |
| Uploaded files | `request.files["file"]` |
| Form values | `request.form["name"]` |
| OpenAPI metadata | route options `summary`, `tags`, `request_body`, `responses` |
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

- Import the request proxy with `from higuma import request`.
- Use `request.args` for query parameters and `request.json` for JSON bodies.
- Use `request.form` and `request.files` for multipart requests.
- A route may return `str`, `bytes`, `dict`, `list`, `Response`, or
  `(body, status, headers)`.
- Use typed path converters: `<int:id>`, `<float:value>`, `<uuid:id>`,
  `<path:filename>`, and `<string:name>`.
- Do not manually serialize JSON unless a custom encoding is required.
- Do not access private `_core` APIs from application code.
- Use `request.raw_headers` only when duplicate header byte pairs are required
  for ASGI/WSGI interoperability.

## WebSocket pattern

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


@dataclass
class CreateUser:
    name: str


@app.post(
    "/users",
    summary="Create user",
    tags=("users",),
    request_body=CreateUser,
    responses={"201": {"description": "Created"}},
)
def create_user():
    return request.json, 201
```

Prefer real Python annotations and dataclasses. higuma resolves postponed
annotations and converts them to OpenAPI 3.1 schemas.

## Verification checklist

Run all checks before presenting a change:

```bash
python -m ruff format --check python tests examples
python -m ruff check python tests examples
python -m pytest
cargo fmt --check
cargo check
cargo test --target x86_64-pc-windows-msvc
mkdocs build --strict
```

For a feature touching the network, also start a real server and test the
actual protocol. Test-client success alone is insufficient for WebSocket,
supervisor, streaming, and proxy changes.

## Repository map

- `src/lib.rs`: Rust HTTP, routing, WebSocket, SSR execution core
- `python/higuma/app.py`: public application API and dispatch
- `python/higuma/request.py`: request, multipart, uploaded files
- `python/higuma/database.py`: built-in SQLite ORM
- `python/higuma/auth.py`: sessions, login, OAuth
- `python/higuma/security.py`: password, token, CSRF, rate limit helpers
- `python/higuma/supervisor.py`: multi-process TCP supervisor
- `examples/`: runnable examples
- `docs/`: Japanese docs
- `docs/en/`: English docs
- `tests/`: behavior tests

## Compatibility expectations

- Preserve existing Flask-like route syntax.
- Add new public names to `python/higuma/__init__.py`.
- Keep runtime dependencies at zero unless a feature cannot reasonably be
  implemented with the standard library or Rust core.
- Update tests, Japanese docs, English docs, examples, changelog, and version
  together for a public feature.
- Treat response `Content-Type` as part of API correctness.
- Never set `Content-Length` manually; the Rust core derives it.
- Use `secure_filename()` for any user-visible upload name.
