# Getting started

## Requirements

- Python 3.10 or newer
- Windows, Linux, or macOS

```bash
python -m pip install -U higuma
```

Create `app.py`:

```python
from higuma import Higuma, request

app = Higuma(__name__)


@app.get("/")
def index():
    return "<h1>Hello higuma</h1>"


@app.get("/users/<int:user_id>")
def user(user_id: int):
    return {"user_id": user_id}


@app.post("/echo")
def echo():
    return {"received": request.json}


if __name__ == "__main__":
    app.run()
```

```bash
python app.py
```

- Application: <http://127.0.0.1:8000>
- Swagger UI: <http://127.0.0.1:8000/docs>
- OpenAPI JSON: <http://127.0.0.1:8000/openapi.json>

## CLI

```bash
higuma run app:app --host 127.0.0.1 --port 8000
higuma routes app:app
higuma run app:app --processes 4
```

Routes may return strings, bytes, dictionaries, lists, response objects, or
`(body, status, headers)` tuples.

## Migrating from 0.2 to 0.3

- Use a secret of at least 32 bytes with `SessionMiddleware`, `AuthManager`,
  and `TokenSigner`.
- Replace an intentional unfiltered `query.delete()` with `query.delete_all()`.
- Set `allowed_origins=(...)` for cross-origin WebSockets.
- Add `ProxyHeadersMiddleware` before using reverse-proxy headers.
