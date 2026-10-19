# Flask compatibility and ecosystem

higuma is an independent Flask-inspired framework, not a drop-in replacement.
Mount an existing Flask application through WSGI and write new endpoints with
higuma for a gradual migration.

## Mount a real Flask application

```bash
python -m pip install "higuma[compat]"
higuma run examples.flask_mount:create_app
```

Outside this repository, use your own module name. The optional `compat` extra
installs Flask 3.1 for integration testing. The base Python runtime still has zero
dependencies.

```python
from flask import Flask, url_for
from higuma import Higuma

legacy = Flask(__name__)

@legacy.get("/hello/<name>")
def hello(name):
    return {"name": name, "url": url_for("hello", name=name)}

app = Higuma(__name__)
app.mount_wsgi("/legacy", legacy)
```

Try `/legacy/hello/%E6%97%A5%E6%9C%AC`. Flask receives a decoded Unicode path,
and its `url_for` includes `/legacy`. Cookies, JSON bodies, and redirects are
preserved. Inside Flask views, use Flask's own request/session proxies. Initialize
Flask extensions on the Flask application and test each integration separately.

## Flask-style APIs in higuma

| API | Behavior |
| --- | --- |
| `with app.app_context():` | Activate `current_app` and `g`; restore outer contexts on exit |
| `g.name`, `g.get/pop/setdefault` | App-context-local state, normally isolated per request |
| `app.extensions` | App-wide extension storage, not request-specific state |
| `@app.teardown_appcontext` | Reverse-order cleanup with an unhandled exception or `None` |
| `has_app_context()`, `has_request_context()` | Check context availability |
| `session[key]` | Proxy to the signed cookie session installed by `SessionMiddleware` |
| `flash(message, category="message")` | Store a message for a subsequent request |
| `get_flashed_messages(with_categories=False, category_filter=())` | Pop messages once; repeat reads in one request use the cached messages |
| `url_for(endpoint, **values)` | Path values, extra query/list values, `_anchor`, `_external`, `_scheme` |
| `request.endpoint`, `request.blueprint` | Matched endpoint and blueprint, or `None` |
| `app.register_error_handler(code_or_exception, handler)` | Register a handler without a decorator |
| `render_template(...)` | Apply app context processors when a context is active |
| `response.headers` | Case-insensitive header reads, writes, deletion, get/pop |
| `higuma run module:create_app()` | No-argument factory syntax, without evaluating arbitrary expressions |

Relative endpoints such as `url_for(".detail", id=1)` require a blueprint request.
Extra query values of `None` are omitted. `_scheme` requires `_external=True`.
Outside a request, external URLs require `app.config["SERVER_NAME"]`. Configure
`TrustedHostMiddleware` before trusting a request's Host for external URLs.

## Sessions and flash messages

```python
import os
from higuma import (
    CSRFProtection, Higuma, SessionMiddleware, csrf_token,
    flash, get_flashed_messages, session,
)

app = Higuma(__name__)
app.add_middleware(SessionMiddleware, os.environ["HIGUMA_SECRET_KEY"])
app.add_middleware(CSRFProtection)

@app.get("/")
def index():
    return {"messages": get_flashed_messages(), "csrf_token": csrf_token()}

@app.post("/save")
def save():
    session["visits"] = session.get("visits", 0) + 1
    flash("Saved", "success")
    return {"ok": True}
```

Keep the cookie returned by GET and send its token in the POST `X-CSRF-Token`
header. Cookies are signed, not encrypted: do not store secrets or large messages.
Use `SessionMiddleware(..., secure=True)` for production HTTPS.
See `examples/flask_compat.py` for an executable factory.

## Compatibility boundaries

- No complete compatibility with Flask signals, Werkzeug routing, Flask CLI
  extensions, or `test_request_context`. Extension `init_app(higuma_app)` is not guaranteed.
- Templates use MiniJinja, not all Jinja2 filters or Python callable globals.
  Build URLs and flash messages in the view and pass them as template values.
- Mounted WSGI/ASGI applications buffer HTTP bodies. They do not provide mounted
  WebSockets, ASGI lifespan, indefinite streams, or SSE. Use native higuma APIs.
- ASGI receives a decoded `path` and the original percent-encoded `raw_path`.
- Input headers containing underscores are dropped at the WSGI boundary to
  avoid normalization collisions with hyphenated headers.
- Teardown runs when dispatch/context exits, before lazy streams/background work.
  Open resources within those operations; do not reuse a connection closed by teardown.
- Rate limits are process-local. At `max_keys`, new identities share a bounded
  overflow bucket instead of evicting active histories. They stay in that bucket
  until its history expires, even if a dedicated slot becomes free. New users may
  share quota under load. Use a proxy for coordinated multi-worker limits.

References: [Flask application context](https://flask.palletsprojects.com/en/stable/appcontext/),
[WSGI PEP 3333](https://peps.python.org/pep-3333/), and
[ASGI HTTP scope](https://asgi.readthedocs.io/en/stable/specs/www.html).
