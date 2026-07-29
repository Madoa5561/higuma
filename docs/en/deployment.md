# Deployment

## Multi-process supervisor

```bash
higuma run app:app --host 0.0.0.0 --port 8000 --processes 4 --max-connections 1024
```

The parent process runs a TCP load balancer and distributes connections to
isolated Rust worker processes. Both HTTP and WebSocket traffic pass through.
Unexpected worker exits are restarted within a bounded restart window.
Connections beyond `--max-connections` are rejected instead of creating
unbounded proxy threads.

## Reverse proxy

Terminate TLS with Cloudflare, Caddy, nginx, or an equivalent trusted proxy.
Configure trusted hosts, exact CORS origins, and secure cookies.

```python
app.add_middleware(TrustedHostMiddleware, ("example.com", "*.example.com"))
app.add_middleware(
    ProxyHeadersMiddleware,
    trusted_proxies=("127.0.0.1",),
)
```

## Secrets

Load secrets from environment variables or a secret manager:

```text
HIGUMA_SECRET_KEY=...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
```

## Checklist

- Enforce HTTPS.
- Keep `debug=False`.
- Use an unpredictable secret key containing at least 32 bytes.
- Back up the SQLite database.
- Limit upload size and destination.
- Ensure the reverse proxy overwrites forwarded client headers.
- Run load tests and a security assessment.
