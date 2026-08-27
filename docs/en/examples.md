# Examples

`examples/` is split by topic so each concept stays small and runnable.

| File | Covers | Runtime note |
| --- | --- | --- |
| `app.py` | Routing, JSON, SSR templates | None |
| `blueprint_hooks.py` | Blueprints, config, request/lifecycle hooks | None |
| `routing_async.py` | Converters, multiple methods, async handlers | None |
| `modern_typed_api.py` | Annotated inputs, DI, response models, OpenAPI | None |
| `class_based_views.py` | View and MethodView | None |
| `streaming_sse.py` | Sync streaming, async SSE, background tasks | SSE ends after three events |
| `lifespan_background.py` | Async lifespan, app.state, background tasks | None |
| `responses_cookies_files.py` | Response types, cookies, file downloads | Serves a repository template |
| `websocket_chat.py` | JSON WebSocket echo | Needs a WebSocket client |
| `multipart_upload.py` | Safe file upload and persistence | Creates `uploads/` on request |
| `mounted_apps.py` | WSGI and ASGI mounts | HTTP scopes only |
| `openapi_api.py` | Legacy dataclass and OpenAPI metadata | Metadata-only form |
| `orm_blog.py` | SQLite ORM CRUD | Creates `blog.sqlite3` at startup |
| `orm_fields.py` | Every ORM field, queries, and save | In-memory SQLite only |
| `authentication.py` | Login, CSRF, passwords, sessions | Requires a secret environment variable |
| `security_tokens_roles.py` | Signed tokens, roles, permissions | Requires a secret environment variable |
| `oauth_login.py` | Google OAuth with PKCE/state | Needs credentials and network access |
| `security_hardened.py` | CORS, hosts, headers, proxy, rate limit | Match proxy trust to your environment |
| `testing_client.py` | JSON, cookie jar, multipart tests | Runs assertions without a server |
| `supervised_app.py` | Multi-process Supervisor | Workers see the peer as loopback |

## Basic execution

Install from the repository root, then run an example:

```bash
python -m pip install -e .
python examples/routing_async.py
python examples/modern_typed_api.py
python examples/streaming_sse.py
python examples/testing_client.py
```

Factory-style examples also work through the CLI:

```bash
higuma run examples.orm_blog:create_app --port 8000
higuma run examples.blueprint_hooks:create_app --port 8000
higuma run examples.supervised_app:app --processes 4
```

## Examples that use secrets

Set a random development value of at least 32 bytes. Do not save a real secret
in shell history or the repository.

=== "PowerShell"

    ```powershell
    $env:HIGUMA_SECRET_KEY = python -c "import secrets; print(secrets.token_urlsafe(32))"
    python examples/authentication.py
    ```

=== "POSIX shell"

    ```bash
    export HIGUMA_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
    python examples/authentication.py
    ```

For the CSRF-protected login, first call `GET /csrf` to create a token in the
same client session. Send that value to `POST /login` in the `X-CSRF-Token`
header or `_csrf_token` form field.

The OAuth example additionally requires `GOOGLE_CLIENT_ID` and
`GOOGLE_CLIENT_SECRET`. Register its exact redirect URI with the provider. The
example makes real requests to Google.

## Examples that create files

`multipart_upload.py` stores files in `examples/uploads/` by default. Set
`HIGUMA_UPLOAD_DIR` to use a dedicated directory. The example normalizes names
and checks a 2 MiB limit and an extension allowlist; production systems should
also add content inspection, malware scanning, authorization, quotas, and
object storage.

`orm_blog.py` creates `examples/blog.sqlite3`. Run `orm_fields.py` for an ORM
exercise that uses only in-memory SQLite.

See [Deployment](deployment.md) for the production Supervisor topology. A
directly exposed Supervisor cannot support client-IP rate limits or audit logs.

[Browse examples on GitHub](https://github.com/Madoa5561/higuma/tree/main/examples)
