# APIリファレンス

このページはhiguma `0.1.0` の公開APIをまとめています。

## Application

### `Higuma`

```python
Higuma(
    import_name,
    *,
    template_folder="templates",
    static_folder="static",
    static_url_path="/static",
)
```

主なmethod:

| Method | 用途 |
|---|---|
| `route(rule, methods=..., endpoint=...)` | route登録 |
| `get/post/put/patch/delete(rule)` | HTTP method別decorator |
| `register_blueprint(blueprint, url_prefix=...)` | Blueprint登録 |
| `url_for(endpoint, **values)` | URL生成 |
| `render_template(name, **context)` | SSR response生成 |
| `jsonify(*args, **kwargs)` | JSON response生成 |
| `make_response(value)` | response正規化 |
| `test_client()` | in-process test client |
| `run(host=..., port=...)` | HTTP server起動 |

## Request

```python
from higuma import Request, request
```

`request` は現在のrequestを指すcontext-local proxyです。handler引数として `Request` を
受け取ることもできます。

## Response

```python
from higuma import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    TemplateResponse,
)
```

helper:

- `jsonify(...)`
- `make_response(...)`
- `redirect(location, status=302)`
- `render_template(template_name, **context)`
- `send_file(path, ...)`

## Routing

```python
from higuma import Blueprint
```

`Blueprint(name, import_name, url_prefix="")` はroute、error handler、hooksを機能単位にまとめます。

## Middleware

```python
from higuma import (
    CORSMiddleware,
    SecurityHeadersMiddleware,
    SessionMiddleware,
    TrustedHostMiddleware,
)
```

## Exceptions

```python
from higuma import abort

abort(404)
abort(400, "Invalid request")
```

公開されるHTTP exception:

- `BadRequest` (400)
- `Unauthorized` (401)
- `Forbidden` (403)
- `NotFound` (404)
- `MethodNotAllowed` (405)
- `Conflict` (409)
- `RequestEntityTooLarge` (413)
- `UnsupportedMediaType` (415)
- `TooManyRequests` (429)
- `InternalServerError` (500)

## Config

`app.config` はmappingとして利用でき、Python object、file、環境変数から値を読み込めます。

```python
app.config.from_mapping(DEBUG=False, MAX_BODY_SIZE=1024 * 1024)
app.config.from_envvar("HIGUMA_SETTINGS")
app.config.from_prefixed_env(prefix="HIGUMA")
```

## CLI

```bash
higuma --version
higuma routes app:app
higuma run app:app --host 127.0.0.1 --port 8000
```
