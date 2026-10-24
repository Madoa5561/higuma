# Testing and request contexts

This guide includes helpers introduced in 0.7.0. Test dispatched views, middleware,
CSRF, and sessions with the test client; context-dependent utilities with
`test_request_context`; and network contracts against a real server.

## Dispatch with the test client

```python
from higuma import Higuma, request

app = Higuma(__name__, static_folder=None)

@app.post("/echo")
def echo():
    return {"value": request.get_json(), "secure": request.scheme == "https"}

client = app.test_client()
response = client.post("/echo", json={"name": "bear"}, base_url="https://example.test")
assert response.status_code == 200
assert response.is_json
assert response.get_json() == {"value": {"name": "bear"}, "secure": True}
```

Use `get/post/put/patch/delete/options/head` or `open(path, method="GET", ...)`.
`response.get_data(as_text=True)` returns text; `.data`/`get_data()` return bytes.

| Argument | Behavior |
| --- | --- |
| `headers` | Header mapping with case-insensitive names |
| `query` | Mapping or pairs; list values become repeated query parameters |
| `query_string` (0.7+) | Str, ASCII bytes, mapping, or pairs; mutually exclusive with `query` |
| `base_url` (0.7+) | HTTP(S) origin such as `https://example.test:8443`; no path prefix or credentials |
| `content_type` (0.7+) | Set Content-Type |
| `json` | Serialize a value; non-None values cannot be combined with `data/files` |
| `data` | Str, bytes, or form mapping; list values become repeated form parameters |
| `files` | `{"file": ("bear.txt", b"hello", "text/plain")}`; use a list of tuples for multiple files |
| `follow_redirects` | Default False; follow same-host HTTP(S) relative/absolute redirects |
| `max_redirects` | Default 20; loops and limit exhaustion raise `RuntimeError` |

`query`/`query_string` replaces query text in the path. `json=None` means no JSON
body. Send JSON null with `data="null", content_type="application/json"`.
303 changes to GET (HEAD stays HEAD); 301/302 change non-GET/HEAD methods to GET.
307/308 preserve the method and body. Final response `.history` is a tuple of
intermediate responses. External hosts are rejected, never contacted.

## Activate a context without dispatch

```python
from higuma import Higuma, has_request_context, request, url_for

app = Higuma(__name__, static_folder=None)

@app.get("/bears/<int:bear_id>")
def bear(bear_id):
    return {"id": bear_id}

with app.test_request_context("/bears/7", query_string={"name": "higuma"}) as current:
    assert request.view_args == {"bear_id": 7}
    assert request.endpoint == "bear"
    assert current.args["name"] == "higuma"
    assert url_for("bear", bear_id=9) == "/bears/9"
assert not has_request_context()
```

The helper accepts request-building arguments above, including `method`, and
sets routing metadata and converted path arguments. Dispatch-only arguments
`follow_redirects/max_redirects` are not accepted. The `with` value is a Higuma
`Request`, not Flask's RequestContext wrapper; push/pop methods are not provided.

It does not execute views, middleware, before/after hooks, CSRF, or session
loading. Use the test client when testing sessions, so `SessionMiddleware` runs.
Exit invokes `teardown_request`, plus `teardown_appcontext` only if a new app
context was needed. Nested contexts restore the outer request and `g`.

You can also activate a manually constructed request:

```python
from higuma import Request

with app.request_context(Request({"path": "/manual", "method": "GET"})):
    assert request.path == "/manual"
```

`request_context` does not accept a WSGI environ or automatically match routes.
Use `test_request_context` when routing metadata is needed.

## Lifespan and sessions

`with app.test_client() as client:` runs startup/shutdown and lifespan. Unlike
Flask's client context, it does not preserve the last request context. Inspect
the response after a request, not the request proxy or `g`.

See the [lifecycle example walkthrough](examples.md#request-lifecycle) for
session/CSRF/Blueprint integration. The cookie jar stores names and values; it
does not fully emulate browser Domain/Path/Secure/SameSite or expiry rules.
Also verify HTTPS, CORS, and cookie restrictions in a real browser.

## Repository regression tests

```bash
python -m pytest tests/test_flask_helpers.py tests/test_flask_helpers_server.py
python -m pytest
```

With the `compat` extra installed, common behavior runs against real Flask too.
Otherwise only Flask cases skip. The server test starts a separate Rust server
process and checks CSRF rejection, Unicode JSON, session cookies, Blueprint
headers, and teardown through real HTTP; the process is stopped on exit.

The test client materializes streams/files/templates in memory. It cannot prove
stream timing, disconnect behavior, backpressure, WebSocket handshakes, proxies,
or TLS. Use real-socket tests and fresh-install smoke checks of published wheels.
