# Deployment

## Choose a topology

A single process is suitable for development and small internal services. To
use multiple CPU cores in production, place Higuma's multi-process Supervisor
behind a trusted reverse proxy that terminates TLS.

```text
Internet
  -> trusted reverse proxy (TLS termination, overwrites forwarded headers)
  -> Higuma Supervisor :8000
  -> Rust worker 127.0.0.1:<ephemeral port>
```

```bash
higuma run app:app \
  --host 127.0.0.1 \
  --port 8000 \
  --processes 4 \
  --max-connections 1024
```

The Supervisor acts as a TCP load balancer and distributes HTTP and WebSocket
connections to isolated Rust workers. Unexpected exits are restarted within
the configured restart window and limit. Connections beyond
`--max-connections` are rejected instead of creating unbounded proxy threads.

## Important client-IP constraint

Supervisor-to-worker connections use loopback. The direct socket peer seen by
each worker is therefore always `127.0.0.1` (or `::1` for an IPv6 setup), not
the original client. The Supervisor does not generate `X-Forwarded-For`.

In production, the outer trusted reverse proxy must **overwrite**
`X-Forwarded-For` and `X-Forwarded-Proto`; it must not accept values supplied
by the client. At the worker, trust only the Supervisor loopback address that
connects directly to it.

```python
from higuma import Higuma, ProxyHeadersMiddleware, TrustedHostMiddleware

app = Higuma(__name__)
app.add_middleware(
    ProxyHeadersMiddleware,
    trusted_proxies=("127.0.0.1", "::1"),
)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=("example.com", "*.example.com"),
)
```

Do not use a proxy that merely appends to untrusted incoming headers, and do
not configure a broad proxy trust range. Either allows clients to spoof their
source address.

!!! warning "Do not expose the Supervisor directly"
    When the Supervisor is exposed directly to the Internet, every connection
    appears as loopback at the worker. The real client address cannot be used
    for `RateLimitMiddleware` keys or audit logs, and all users are treated as
    one client. Production deployments that need client IPs require the trusted
    reverse proxy and `ProxyHeadersMiddleware` arrangement above.

## TLS, hosts, and cookies

Terminate TLS with Cloudflare, Caddy, nginx, or another trusted reverse proxy.

- Restrict public hosts with `TrustedHostMiddleware`.
- List exact CORS origins rather than using a wildcard.
- Set session cookies to `secure=True`, `httponly=True`, and an appropriate
  `samesite` policy.
- Use `SecurityHeadersMiddleware` and tune CSP for the application.
- Limit upload size, accepted extensions, and destination paths.

## Workers and application state

Startup/shutdown hooks, in-memory caches, and rate-limit counters are local to
each worker. Put state that must be shared across workers in an external
durable store. If multiple workers write to SQLite, plan for write contention,
backup, and recovery; consider a client/server database as load grows.

Synchronous WebSocket handlers have a per-worker concurrency limit. Move
long-running blocking work to async handlers or external jobs, and load-test
both HTTP and WebSocket traffic.

## Graceful shutdown

On Unix, `SIGTERM` starts graceful shutdown. Configure the orchestrator's stop
grace period to include time for active requests and WebSockets to close. Make
handlers idempotent and keep persistence transactions short so forced exits
remain recoverable.

## Secrets

Read secrets from environment variables or a secret manager, never from files
committed to the repository.

```text
HIGUMA_SECRET_KEY=...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
```

Use different values in development and production, and prepare to rotate
cookie-signing keys and OAuth client secrets.

## Checklist

- Expose only the reverse proxy; bind the Supervisor privately or to loopback.
- Make the proxy overwrite forwarded headers and trust only loopback at workers.
- Enforce HTTPS and keep `debug=False`.
- Use an unpredictable secret key containing at least 32 bytes.
- Define host, CORS, cookie, and CSRF policies.
- Set request/upload limits and safe storage destinations.
- Test database backup and restore.
- Load-test worker count, connection limits, and restart limits.
- Exercise shutdown, worker crashes, and WebSocket disconnects.
- Track security updates for dependencies and Higuma.
