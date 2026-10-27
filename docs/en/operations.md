# Async execution, shared state and operations (0.10+)

The base package still has zero mandatory Python dependencies. Install only the extras you use:
The 0.10 development features in this chapter are included in 0.11.0.
Run files in `examples/` from a repository checkout, not from the installed wheel.

```bash
python -m pip install "higuma[asgi,async-db,integrations,observability]==0.11.0"
```

## Conservative ASGI async fast path

Eligible `async def` HTTP routes run on the server loop and send ordinary responses without
coordination-worker round trips. Set `app.config["ASGI_ASYNC_FAST_PATH"] = False` to restore the bridge.
Eligibility requires no middleware, app/Blueprint hooks, custom error handlers, app teardown,
relevant lifecycle signal subscribers, `Depends`/`BackgroundTasks`/`Form`/`File` parameters, or disk-spooled input.
Otherwise Higuma uses its compatibility path; authentication and hooks are never skipped.
Typed path/query/body inputs, request/g, response models and request-local after callbacks remain available.
Files, templates, streams and background work use the compatibility serializer, with async work on the server loop.
`ASGI_MAX_CONCURRENCY` applies to both paths. Native, WSGI and WebSocket entry points are unchanged.

Ingress still completes before dispatch. Blocking calls inside an async handler still block:
use async drivers or explicit offloading. This does not promise every ASGI workload is faster
or that Higuma always outperforms Starlette. See [measurements](performance.md).

## Async SQLAlchemy

```python
from typing import Annotated, Any
from sqlalchemy import text
from higuma import AsyncSQLAlchemyExtension, Depends, Higuma

app = Higuma(__name__)
database = AsyncSQLAlchemyExtension()
database.init_app(app, "sqlite+aiosqlite://")

@app.get("/db")
async def probe(db: Annotated[Any, Depends(database.session)]):
    return {"value": await db.scalar(text("select 1"))}

asgi_app = app.asgi_app
```

Dependency sessions close and roll back uncommitted work. For writes use
`async with database.transaction() as db:` and finish committing before returning success.
Exceptions and cancellation roll back. Never share a Session between concurrent tasks.
App-isolated engines/session factories start and dispose on their owning ASGI loop. Enable lifespan;
use `with app.app_context():` when accessing `database.engine` from startup hooks.
Initialization does not modify schemas. Migrations, production drivers and pool sizing remain explicit.
Do not share these resources across native/WSGI loops. Tests use SQLite, not all production databases.

`python -m examples.async_ecosystem` starts a disposable in-memory SQLite demo with
`GET/POST /notes`, health endpoints and response request IDs. POST `{"title":"bear"}`.
It is an unauthenticated localhost demo, not a production application.

## Flask-style lifecycle signals

```python
from higuma import request_finished, template_rendered

@request_finished.connect_via(app)
def completed(sender, response):
    sender.logger.debug("status=%s", response.status_code)

def capture(sender, template, context):
    assert template == "page.html"

with template_rendered.connected_to(capture, app):
    app.test_client().get("/")
```

Public APIs are `Signal`, `ANY`, connect/disconnect/connect_via/connected_to/send,
receivers_for and has_receivers_for. Weak references are the default, including bound methods
and senders. Decorator and temporary context subscriptions hold callbacks strongly. Scope senders to an app.
Receivers are synchronous and exceptions propagate. Async receivers and Blinker internals are not supported.

Events: request_started, request_finished(response), request_tearing_down(exc),
got_request_exception(exception), appcontext_pushed/appcontext_tearing_down(exc)/appcontext_popped,
before_render_template/template_rendered(template, context), and message_flashed(message, category).
Template is a name string, not a Jinja Template object. With template subscribers, rendering occurs
during dispatch and the success event fires only after rendering succeeds.
Request event order is compared against Flask, but signal objects are not shared with Flask/Blinker;
this is not complete extension compatibility. See `examples/signals.py`. Do not log complete contexts/cookies.
request_started precedes Higuma middleware, so session/user may not be loaded yet.

## Redis-backed sessions and distributed quotas

```python
from higuma import RedisExtension, RedisSessionStore, RedisSessionMiddleware, RedisRateLimitMiddleware

cache = RedisExtension()
cache.init_app(app, "redis://127.0.0.1:6379/0", async_client=False)
store = RedisSessionStore(cache.client, prefix="my-service:", ttl=3600)
app.add_middleware(RedisSessionMiddleware, secret_key, store=store, secure=True)
app.add_middleware(RedisRateLimitMiddleware, cache.client, prefix="my-service:", limit=100, window=60)
```

Use securely provisioned secret material with at least 32 bytes of entropy. AuthManager accepts
`session_middleware=RedisSessionMiddleware` and `session_options={"store": store, "secure": True}`;
do not register two session middleware instances.

- Cookies contain only signed random IDs; data lives in Redis. This is not encryption.
- Values must be JSON-serializable. Arbitrary Python objects are not pickled.
- A unique app prefix is mandatory. Default TTL is 14 days and payload limit is 64KiB.
  Permanent cookie max_age must match TTL.
- Clearing then logging in rotates IDs. Logout removes the old record. Operators can revoke
  a previously recorded `session.sid` with `store.revoke(sid)`. Per-user session indexes/admin UI are application responsibilities.
- Redis CAS rejects saving a record revoked/changed since it was loaded, returning 409.
  It cannot retroactively stop authorization already loaded by an in-flight request.
- Writes refresh TTL; reads do not. Expiration/eviction requires a new login.
- Quotas use atomic fixed windows, unlike the existing process-local sliding-window limiter.
  Identity keys are SHA-256 hashes, not guaranteed anonymization. TTL, capacity planning and upstream connection limits remain necessary.
- Redis failure returns 503, not a fallback to cookies or process-local quotas. Configure TLS/ACL,
  timeouts, storage boundaries, secrets and trusted proxy handling explicitly.

These middleware use synchronous Redis clients, including on ASGI compatibility workers.
CSRF protection is separate. `python -m examples.redis_sessions` is a localhost-only Redis demo:
obtain a cookie/token with `GET /csrf`, then POST `/visits` or `/logout` with `X-CSRF-Token`.

## Logs, metrics and tracing

```python
from higuma import Observability

observer = Observability()  # No external exports, tracing or logging by default
asgi_app = observer.wrap_asgi(app)
print(observer.snapshot())  # Expose only through protected operational access
```

The ASGI wrapper measures complete streams/disconnects and preserves the fast path. For native/WSGI,
`app.add_middleware(observer)` measures the middleware call, not lazy stream completion. Do not register both.
Request IDs are server-generated by default and returned as X-Request-ID. ASGI stores them in scope.state;
native middleware stores them in request.state. Only `trust_request_id=True` accepts supplied IDs,
restricted to 64 ASCII letters/digits/underscore/hyphen characters.

Metrics aggregate route patterns, known methods, statuses and outcomes. Beyond max_series (default 1024),
new series share an overflow bucket. Counts, duration sum/max and in-flight are process-local;
there is no automatic multi-process aggregation or histogram. No metrics endpoint is published automatically.
Pass `logger=...` for structured records and use JSONLogFormatter for JSON. The observer does not record
query strings, concrete path parameters, bodies, cookies, authorization headers or exception text.
It does not redact arbitrary application logs/exporters.
`tracing=True` uses optional OpenTelemetry APIs; providers, exporters, destinations and credentials
must be explicitly configured by the application. It does not open an external connection or read credentials
automatically. Configure batching for slow exporters/loggers.

## Liveness and readiness

```python
from higuma import HealthChecks

health = HealthChecks(timeout=1.0)
health.add_check("cache", cache_probe)  # Zero-argument sync/async callback returning True
health.install(app)
```

GET /health/live is independent of dependencies; GET /health/ready returns 200 only if every probe succeeds,
otherwise 503. Responses contain only generic status and no-store headers, never connection/exception details.
Set health.ready=False to drain. Timed-out probes are not requeued indefinitely; sync probe threads are bounded.
Python cannot forcibly terminate an uncooperative blocking function. Set I/O timeouts inside probes too.
Async code must cooperate with cancellation as well.
Readiness does not perform migrations or prove the complete application flow works.

## Validation and dependency audits

Feature suites are test_async_fast_path.py, test_async_database.py, test_signals.py,
test_redis_state.py and test_observability.py. Redis communication uses an isolated HIGUMA_TEST_REDIS_URL
and cleans only UUID-prefixed test keys. CI audits target runtime dependencies separately from Python 3.14
build/audit tooling, without ignored advisories. Installing audit tooling on older Python versions can
resolve unsupported/unpatched tool dependencies.

Upstream: [SQLAlchemy asyncio](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html),
[Flask Signals](https://flask.palletsprojects.com/en/stable/signals/),
[Redis EVAL](https://redis.io/docs/latest/commands/eval/),
[OpenTelemetry Python](https://opentelemetry.io/docs/languages/python/instrumentation/).
