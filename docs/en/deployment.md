# Deployment

## Multi-process supervisor

```bash
higuma run app:app --host 0.0.0.0 --port 8000 --processes 4
```

The parent process runs a TCP load balancer and distributes connections to
isolated Rust worker processes. Both HTTP and WebSocket traffic pass through.

## Reverse proxy

Terminate TLS with Cloudflare, Caddy, nginx, or an equivalent trusted proxy.
Configure trusted hosts, exact CORS origins, and secure cookies.

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
- Use an unpredictable secret key.
- Back up the SQLite database.
- Limit upload size and destination.
- Ensure the reverse proxy overwrites forwarded client headers.
- Run load tests and a security assessment.
