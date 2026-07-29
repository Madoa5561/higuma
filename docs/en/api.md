# API reference

## Application

`Higuma(import_name, template_folder="templates", static_folder="static",
openapi_url="/openapi.json", docs_url="/docs")`

- `route`, `get`, `post`, `put`, `patch`, `delete`
- `websocket`
- `mount_wsgi`, `mount_asgi`
- `render_template`, `jsonify`, `url_for`
- `openapi`, `clear_template_cache`
- `init_database`
- `before_request`, `after_request`, `middleware`, `errorhandler`
- `run(host, port, workers, processes, app_ref)`
- `test_client`

## Request

- `request.method`, `path`, `args`, `headers`, `cookies`
- `request.json`, `form`, `files`, `body`, `text`
- `request.path_params`, `client_addr`, `state`, `session`, `user`

## WebSocket

- `send`, `send_text`, `send_bytes`, `send_json`
- `receive`, `receive_text`, `receive_bytes`, `receive_json`
- `close`
- `WebSocketDisconnect`

## Database

- `Database`, `Model`, `Session`, `Query`
- `Field`, `Integer`, `Float`, `String`, `Boolean`, `Date`, `DateTime`, `Blob`

## Auth and security

- `AuthManager`, `current_user`, `login_required`, `AnonymousUser`
- `PasswordHasher`, `TokenSigner`
- `CSRFProtection`, `csrf_token`
- `RateLimitMiddleware`
- `generate_user_id`, `validate_user_id`
- `OAuth2Client.google`, `line`, `discord`
- `login_required`, `fresh_login_required`, `roles_required`, `permissions_required`
- `CORSMiddleware`, `SecurityHeadersMiddleware`, `TrustedHostMiddleware`

See [Examples](examples.md) and the
[repository examples](https://github.com/Madoa5561/higuma/tree/main/examples)
for complete runnable applications.
