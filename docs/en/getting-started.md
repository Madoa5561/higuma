# Getting started

## Requirements

- CPython 3.10 or newer
- Windows, Linux, or macOS

```bash
python -m pip install -U higuma
```

Platforms without a published wheel build the source distribution with a Rust
toolchain.

## Create an application

Create `app.py`:

```python
import asyncio

from higuma import Higuma, request

app = Higuma(__name__, max_content_length=8 * 1024 * 1024)


@app.get("/")
def index():
    return "<h1>Hello higuma</h1>"


@app.get("/users/<int:user_id>")
def user(user_id: int):
    return {"user_id": user_id, "verbose": request.args.get("verbose") == "1"}


@app.post("/echo")
def echo():
    data = request.get_json()
    if not isinstance(data, dict):
        return {"error": "JSON object required"}, 400
    return {"received": data}, 201


@app.get("/async")
async def async_handler():
    await asyncio.sleep(0)
    return {"async": True}


if __name__ == "__main__":
    app.run()
```

```bash
python app.py
```

- Application: <http://127.0.0.1:8000>
- Swagger UI: <http://127.0.0.1:8000/docs>
- OpenAPI JSON: <http://127.0.0.1:8000/openapi.json>

`dict` and `list` values become JSON; `str` values become HTML. Explicit
response classes and `(body, status, headers)` tuples are also supported.

```python
from higuma import PlainTextResponse


@app.get("/health")
def health():
    return PlainTextResponse("ok", headers={"cache-control": "no-store"})


@app.get("/created")
def created():
    return {"ok": True}, 201, {"x-example": "higuma"}
```

## Route converters

| Syntax | Python value | Matching path |
| --- | --- | --- |
| `<string:name>` | `str` | One segment without a slash |
| `<int:item_id>` | `int` | A signed integer |
| `<float:value>` | `float` | A decimal number |
| `<uuid:item_id>` | `uuid.UUID` | A UUID |
| `<path:filename>` | `str` | The remaining path, including slashes |

Static routes take priority over dynamic routes. Encoded slashes, invalid
UTF-8, control characters, and ambiguous paths are rejected.

## CLI and application factories

```bash
higuma --version
higuma routes app:app
higuma run app:app --host 127.0.0.1 --port 8000
higuma run app:app --processes 4 --max-connections 1024
```

When the object in `module:object` is callable, the CLI invokes it as a
zero-argument application factory.

```python
def create_app():
    app = Higuma(__name__)
    return app
```

```bash
higuma run app:create_app
```

Multiple processes require an importable `module:object`. When starting
multiple processes from `python app.py`, also pass `app_ref="app:app"`.

## Your first test

The test client uses the same dispatch pipeline without opening a network
socket.

```python
def test_index():
    client = app.test_client()
    response = client.get("/users/42", query={"verbose": "1"})

    assert response.status_code == 200
    assert response.json["user_id"] == 42
```

The test client does not exercise the real protocol for WebSockets, streaming,
the supervisor, or proxy headers. Test those features against a real server too.

## Continue

- [Feature guide](features.md)
- [Examples](examples.md)
- [API reference](api.md)
- [Authentication and security](security-auth.md)

## Migrating from 0.3 to 0.4

- `Query` can now be imported from `higuma` for database query annotations.
- `higuma.Session` is the cookie-session type; use `DatabaseSession` for the
  database-session type.
- OAuth state and PKCE flows now require `SessionMiddleware`.
- With a custom CSRF field, `csrf_token()` follows the active
  `CSRFProtection` configuration.
- Enable runtime input validation and conversion with `Annotated` and markers
  such as `QueryParam` or `Body`.
- `response_model` performs runtime output validation and field filtering.
- A legacy `request_body=` option or return annotation by itself remains schema
  metadata.
- Streaming responses, SSE, background tasks, lifespan, and class-based views
  are available in 0.4.
