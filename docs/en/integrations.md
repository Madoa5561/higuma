# Optional ecosystem integrations (0.9+)

0.10+ also provides [async SQLAlchemy, Redis sessions and shared quotas](operations.md).
The SQLAlchemyExtension described below remains the synchronous adapter.

Install `higuma[integrations]` for SQLAlchemy/redis-py. Base required Python
dependencies remain empty. These are native Higuma adapters, not drop-in
Flask-SQLAlchemy/Flask-extension compatibility.

## SQLAlchemy

```python
from typing import Annotated, Any
from sqlalchemy import text
from higuma import Depends, Higuma, SQLAlchemyExtension

app = Higuma(__name__)
database = SQLAlchemyExtension()
database.init_app(app, "sqlite:///application.sqlite")

@app.get("/health/db")
def health(db: Annotated[Any, Depends(database.session)]):
    return {"value": db.scalar(text("select 1"))}
```

`database.session` yields an isolated dependency session and closes it at cleanup,
rolling back uncommitted work. It never commits after sending an HTTP success.
For writes, use `with database.transaction() as db:` and finish that block before
returning success. Sessions are not shared across requests/threads. One extension
can serve multiple apps with isolated engines/session factories; shutdown disposes
the engine. WSGI workers must manage `app.lifespan()` explicitly.

This adapter targets synchronous SQLAlchemy 2.x. AsyncSession, additional DB
drivers, and migrations are external choices. SQLite tests are not production
PostgreSQL/MySQL verification. `init_app()` does not modify schemas.

## Redis

```python
from higuma import Higuma, RedisExtension

app = Higuma(__name__)
cache = RedisExtension()
cache.init_app(app, "redis://127.0.0.1:6379/0", async_client=True,
               decode_responses=True)

@app.get("/count")
async def count():
    return {"count": await cache.client().incr("example:count")}

asgi_app = app.asgi_app
```

Use `async_client=False` and synchronous handlers for native/WSGI. Async clients
require the shared ASGI loop. Startup creates the client and checks PING by default;
shutdown closes it. `ping_on_startup=False` allows lazy connection. Access before
startup fails, and failed initialization closes its client. Connect/operation
timeouts default to five seconds and accept redis-py option overrides. RESP2 is
explicit to avoid redis-py default-protocol changes.

Run `python -m examples.redis_counter` after starting a dedicated local Redis.
`POST /counter/bear` increments a counter. The demo binds loopback only.

Unauthenticated localhost is a development example only. Configure production
TLS/ACL/secret management and never log credential-bearing URLs. This integration
does not implement durable queues, distributed rate limiting, or Redis sessions.

## Factory-based project example

From the repository checkout:

```bash
python -m pip install -e ".[integrations,asgi]"
higuma --app examples.project:create_demo_app init-db
higuma --app examples.project:create_demo_app create-user bear --admin
higuma --app examples.project:create_demo_app run --port 8000
```

Passwords are prompted privately, never accepted as CLI arguments. Open
`http://127.0.0.1:8000/`, log in, and create notes in the admin page.
`GET/POST /admin/notes/api` share authorization and CSRF checks. SQLite lives at
`target/project-demo/notes.sqlite`. init-db creates missing tables, not migrations.
The demo generates a signing key per process, so restarts invalidate sessions.
For production, provide a stable secret and explicit DB URL through your own
factory calling `create_app(database_url=..., secret_key=...)`; configure secure
HTTPS cookies, trusted hosts/proxies, rate limits, and account management.
Do not publish the demo unchanged.

The example separates factory/models/auth/admin/child notes components and
template/static resources. Tests cover initialization, hashed passwords,
login/logout, CSRF, roles, HTML escaping, and JSON persistence. Password reset,
email verification, and MFA are not implemented.

## Verification

Run `python -m pytest tests/test_integrations.py tests/test_project_example.py`.
Set `HIGUMA_TEST_REDIS_URL` only to isolated test Redis, never production. Tests
remove only their own random-prefixed keys, never FLUSHDB/FLUSHALL. Missing Redis
configuration produces a skip, not proof of real communication. CI provisions a
dedicated Redis service.

Upstream: [SQLAlchemy Session](https://docs.sqlalchemy.org/en/20/orm/session_basics.html),
[redis-py asyncio](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html).
