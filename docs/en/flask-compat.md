# Flask compatibility and ecosystem

higuma is an independent Flask-inspired framework, not a drop-in replacement.
Mount an existing Flask application through WSGI and write new endpoints with
higuma for a gradual migration.

Since 0.8.0, the opposite direction is available too: export Higuma through
`app.asgi_app` / `app.wsgi_app`. See [Server interfaces](server-interfaces.md) for
HTTP, WebSocket, lifespan, and worker boundaries. WSGI compatibility does not
imply complete Flask internal/extension compatibility.

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
| `with app.test_request_context(path, **kwargs):` | 0.7+: Activate contexts without dispatch. [Testing](testing.md) |
| `with app.request_context(request):` | 0.7+: Context from a Higuma `Request` |
| `@app.teardown_request` | 0.7+: Reverse-order cleanup while the request context is active |
| `@after_this_request` | 0.7+: Request-local response callbacks, in registration order |
| `Blueprint.before_request/after_request/teardown_request` | 0.7+: Hooks scoped to the selected Blueprint |
| `Blueprint.errorhandler/register_error_handler/context_processor` | 0.7+: Scoped error handlers and template values |
| `session[key]` | Proxy to the signed cookie session installed by `SessionMiddleware` |
| `flash(message, category="message")` | Store a message for a subsequent request |
| `get_flashed_messages(with_categories=False, category_filter=())` | Pop messages once; repeat reads in one request use the cached messages |
| `url_for(endpoint, **values)` | Path values, extra query/list values, `_anchor`, `_external`, `_scheme` |
| `request.endpoint`, `request.blueprint` | Matched endpoint and blueprint, or `None` |
| `app.register_error_handler(code_or_exception, handler)` | Register a handler without a decorator |
| `render_template(...)` | Apply app context processors when a context is active |
| `response.headers` | Case-insensitive header reads, writes, deletion, get/pop |
| `request.values/is_json/mimetype/host_url/full_path` | 0.7+: Query-first combined inputs, MIME and URL helpers |
| `response.data/get_data/set_data/is_json/get_json` | 0.7+: Buffered body helpers and MIME-aware JSON reads |
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

## Blueprints and request lifecycle (0.7.0+)

```python
from higuma import Blueprint, Higuma, after_this_request, g

app = Higuma(__name__)
pages = Blueprint("pages", __name__, url_prefix="/pages")

@pages.before_request
def prepare():
    g.section = "pages"

@pages.after_request
def scoped_headers(response):
    response.headers["X-Section"] = g.get("section", "pages")
    return response

@pages.teardown_request
def cleanup(error):
    g.pop("section", None)

@pages.get("/")
def index():
    @after_this_request
    def local(response):
        response.headers["X-Local"] = "yes"
        return response
    return {"section": g.section}

app.register_blueprint(pages)
```

For normal HTTP dispatch the order is app before, Blueprint before, view,
request-local after, Blueprint after, app after, session save, Blueprint teardown,
then app teardown. Before hooks and request-local callbacks run in registration
order; scoped/global after and teardown hooks run in reverse order. Sync and async
callbacks are supported. After callbacks must return a response.
Middleware or before hooks can short-circuit the request: cleanup must tolerate
missing initialization in `g`. Session mutations during teardown are not saved.

Blueprint error handlers require a matched route: they can handle `abort(404)`
inside a view, not routing 404/405 for unknown URLs or disallowed methods.
HTTP status handlers take priority (Blueprint before app for the same code),
then exception types are searched. Template processors run app then Blueprint;
explicit template values win. `app.update_template_context(values)` also applies
them. Blueprint names must be non-empty and dot-free; local endpoints must be
dot-free, and registered Blueprint names must be unique.

Session changes made by `after_this_request` or `after_request` are saved onto
the final response, including response replacements and `Vary: Cookie`.
See `examples/flask_lifecycle.py` and its [walkthrough](examples.md#request-lifecycle).

## JSON and input precedence

`request.values` contains only query values for GET, otherwise query then form.
Query values win for a shared key; `getlist()` includes both sources.
Within one source, Higuma's existing MultiDict selects the **last** duplicate
value, unlike Flask/Werkzeug's first-value behavior. Use `getlist()` deliberately.

JSON MIME types are `application/json` and `application/*+json`. Requests parse
bytes directly, supporting UTF-8/16/32. Invalid Unicode/JSON returns 400; a
non-JSON MIME returns 415. A failed silent read does not hide a subsequent error.

Starting in 0.7.0, response `get_json()`/`.json` checks MIME and returns `None`
for non-JSON responses. If you intentionally parsed those bodies before, use
`get_json(force=True)`. `silent=True` suppresses parse errors. Response JSON
results are not cached.

## Compatibility boundaries

- No complete compatibility with Flask signals, Werkzeug routing, nested
  Blueprints, or Flask CLI extensions. Extension `init_app(higuma_app)` is not guaranteed.
- `request_context` accepts a Higuma `Request`, not a WSGI environ. Context helpers
  are `with` managers, not Flask push/pop wrappers. See [Testing](testing.md).
- `request.query_string` is a str. The existing `request.base_url` and new `host_url`
  are origin roots, unlike Flask's path-inclusive `base_url`. `full_path` includes
  path and query, with a trailing `?` for an empty query. `request.path` retains
  percent encoding while route arguments are decoded. Trailing slash routing differs.
- Request bodies are buffered. `get_json(cache=...)`, Flask body stream APIs,
  and complete Werkzeug request/response manipulation are not provided.
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
