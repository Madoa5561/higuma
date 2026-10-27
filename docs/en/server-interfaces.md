# ASGI, WSGI, and the built-in server

Since 0.8.0 the same Higuma application has three deployment entry points.
This is the opposite direction from `mount_asgi` / `mount_wsgi`, which host other applications.

| Area | Built-in Rust server | `app.asgi_app` | `app.wsgi_app` |
| --- | --- | --- | --- |
| HTTP, routing, sessions, auth, templates | Yes | Yes | Yes |
| Response streaming, SSE, files | Yes | Yes | Yes; occupies a worker |
| WebSockets | Yes | Yes | Not in standard WSGI |
| Startup/shutdown and lifespan | Managed by `app.run()` | ASGI lifespan protocol | No standard protocol; explicit worker setup |
| Shared loop-bound async resources | Restricted | Async lifespan and handlers share the server loop | Not generally guaranteed |
| Sockets, TLS, compression, workers | Higuma/proxy | External ASGI server/proxy | External WSGI server/proxy |

The base package still has zero Python runtime dependencies. All modes still
require Higuma's Rust extension. External modes reuse Rust templating, but the
external server handles HTTP transport.

## Export one application

```python
from higuma import Higuma

app = Higuma(__name__)

@app.get("/")
def index():
    return {"framework": "higuma"}

asgi_app = app.asgi_app
wsgi_app = app.wsgi_app
```

Each property returns a stable callable. `app` itself does not ambiguously select
between protocols: pass the explicit entry point. Native deployment still uses `app.run()`.

## Run through ASGI

```bash
python -m pip install "higuma[asgi]"
uvicorn examples.server_modes:asgi_app --host 127.0.0.1 --port 8000 --lifespan on --ws websockets-sansio
```

The module is a repository example; replace it with your own `app:asgi_app`.
The optional extra installs Uvicorn and websockets, neither required for importing
Higuma. Python 3.10 resolves compatible websockets 16.x; newer Python versions
can resolve newer supported releases.

The callable implements ASGI 3 with asyncio HTTP, WebSocket, and lifespan scopes.
It does not promise trio, legacy ASGI 2 callables, or every ASGI extension.
Real-server verification uses Uvicorn; validate other servers' settings/extensions separately.

### Async operations and lifespan

```python
import asyncio
from contextlib import asynccontextmanager
from higuma import Higuma

@asynccontextmanager
async def lifespan(app):
    # Create and close loop-bound clients/pools here.
    yield {"gate": asyncio.Semaphore(10)}

app = Higuma(__name__, lifespan=lifespan)

@app.get("/")
async def index():
    async with app.state["gate"]:
        return {"ready": True}

asgi_app = app.asgi_app
```

Async lifespan, handlers, hooks, dependencies, streams, and background tasks run
on the ASGI server loop. Yielded mappings update `app.state` and, when the server
supports lifespan state, `request.scope["state"]`. Lifecycle failures send failed
events with generic messages; details stay in server logs.

Use one Higuma instance per active ASGI lifespan/event loop. Each process worker
creates its own instance/resources. Do not share loop-bound clients across loops.
Disabling lifespan also disables automatic startup/shutdown and resource management.

### Execution and concurrency limits

To preserve synchronous middleware and Flask-style hooks, a dedicated worker
coordinates dispatch and bridges awaitables back to the server loop. It does not
consume the server's default executor. **Even async handlers use a coordination
worker; this is not a coroutine-only implementation.** Long WebSockets and SSE
consume concurrency slots too. Load-test high-connection-count deployments.

Set these before serving:

| Config | Default | Meaning |
| --- | --- | --- |
| `ASGI_MAX_CONCURRENCY` | `64` | Shared HTTP/WS slots and dedicated worker ceiling; positive integer |
| `ASGI_WEBSOCKET_QUEUE_SIZE` | `4` | Adapter inbound message queue; positive integer |
| `ASGI_WEBSOCKET_MAX_MESSAGE_SIZE` | `1048576` | UTF-8/bytes message limit; oversized messages close with 1009 |
| `MAX_CONTENT_LENGTH` | Constructor value, default 8 MiB | Buffered HTTP request limit |

Also bound the external server's connection counts, message sizes, queues, and
timeouts. The adapter cannot retroactively bound messages/bodies already buffered
by that server. Arbitrary synchronous Python work cannot be forcibly stopped:
disconnects cancel active async work, retain the worker slot until completion,
and run cleanup. Apply timeouts inside blocking operations too.

### WebSockets

Sync handlers keep `send_*` / `receive_*`. Async handlers use the awaitable API:

```python
@app.websocket("/ws", allowed_origins=("https://example.com",))
async def socket(ws):
    value = await ws.areceive_json()
    await ws.asend_json({"echo": value})
    await ws.aclose()
```

`asend_text/asend_bytes/asend_json`, `areceive/areceive_text/areceive_bytes/areceive_json`,
and `aclose(code=1000, reason="")` work in native mode too. Calling synchronous WS
methods on the ASGI event loop raises instead of deadlocking.

Origin and authentication middleware run before accept. HTTP before/after-request
hooks do not run during the WebSocket preflight. HTTP denial responses
require the server's `websocket.http.response` extension; otherwise an upgrade
denial normally becomes 403. Handshake cookie writes require WS ASGI spec 2.1+;
older servers are denied rather than silently losing cookies. Close reasons are
sent only with spec 2.3+. Subprotocol selection and arbitrary WS extensions are
not exposed. Session mutations after upgrade cannot be saved to browser cookies;
perform session changes through HTTP endpoints.

## Run through WSGI

Pass `wsgi_app(environ, start_response)` to an external server. The repository
example uses Python's reference server for local demonstrations:

```bash
python -m examples.server_modes --mode wsgi --port 8000
```

That reference server is not a production recommendation. Real-server checks use
wsgiref and protocol checks use `wsgiref.validate`; validate your chosen production
server and worker configuration separately.

WSGI has no standard startup/shutdown events, so the callable does not start
lifespan automatically. Manage it around the worker lifecycle, such as
`with app.lifespan():`, as the example does. Prefer synchronous resources; do not
assume loop-bound async clients can be shared between WSGI requests.

The response iterable yields bytes and closes streams/files/dependencies through
`close()`. The WSGI server must call it. SSE works but occupies a worker for the
connection lifetime. Standard WSGI cannot upgrade to WebSockets.

Input reads are bounded by `CONTENT_LENGTH`. Without a length, bounded EOF reads
are performed only when the server sets `wsgi.input_terminated=True`; otherwise
the unspecified body is empty rather than risking an indefinite socket read.

## Paths, headers, and middleware

ASGI `root_path` / WSGI `SCRIPT_NAME` feed `request.root_path`, `url_for`, Swagger
UI, default OpenAPI server URLs, and nested application mounts. Supply prefixes
through trusted server configuration; `X-Forwarded-Prefix` is not automatically
trusted. WSGI's decoded `PATH_INFO` cannot fully recover original encoded slashes.
Normalized WSGI input headers cannot recover original duplicates/underscores
either; reject ambiguous inputs at the WSGI server/proxy boundary too.

ASGI duplicate headers remain in `request.raw_headers`; split Cookies are joined
for parsing, while response Set-Cookie headers stay separate. Gateways normalize
framing/hop-by-hop headers and suppress bodies for HEAD and 204/205/304.

Wrap standard ASGI middleware around `app.asgi_app`, or WSGI middleware around
`app.wsgi_app`. These protocols differ from Higuma's own middleware call style.
Higuma's `call_next` is synchronous: do not invoke it directly from an async
middleware chain on the ASGI loop. Implement async chains as standard ASGI
middleware outside the entry point instead.
Compatibility with Flask/Starlette internal extension APIs is not implied.
See [Flask compatibility](flask-compat.md).

## Remaining boundaries

Since 0.9, ordinary native/WSGI async HTTP work reuses a process-local auxiliary
loop and closes request await contexts after response work. It is not necessarily
the ASGI server, native lifespan, or native WS-connection loop. Reentrant native
async middleware using synchronous `call_next` may use a legacy thread bridge.
Prefer ASGI lifespan for shared loop-bound resources.

- Ingress completes before dispatch; 0.9+ [spooling/stored-body iterators](uploads.md) are not live network-body iteration.
- Responses send chunk-by-chunk, awaiting ASGI `send()`. External file responses
  read chunks in Python, not the Rust HTTP transport; identical performance,
  gzip behavior, or zero-copy is not promised.
- `mount_wsgi` / `mount_asgi` remain buffered HTTP integrations. External ASGI
  deployment does not enable mounted WS, lifespan forwarding, or infinite streams.
- Teardown runs after dispatch. Do not reuse teardown-closed `g` resources inside
  lazy streams. Yield-dependency cleanup follows response close/background work.
- Existing background-task, cookie-session, process-local rate-limit, and SQLite
  limitations remain unchanged.
- TLS, HTTP/2/3, compression, proxy trust, workers, and reload belong to the external
  server/proxy. Higuma's native server CLI options do not configure those servers.

Specifications: [ASGI](https://asgi.readthedocs.io/en/stable/specs/main.html),
[lifespan](https://asgi.readthedocs.io/en/stable/specs/lifespan.html),
[WSGI PEP 3333](https://peps.python.org/pep-3333/).
