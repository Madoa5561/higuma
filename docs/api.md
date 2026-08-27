# APIリファレンス

ここに記載する名前は、特記がなければ`from higuma import ...`でimportできます。
インストール中の版は`higuma.__version__`で確認できます。

## Applicationとrouting

### `Higuma`

```python
Higuma(
    import_name,
    *,
    template_folder="templates",
    static_folder="static",
    static_url_path="/static",
    max_content_length=8 * 1024 * 1024,
    debug=False,
    openapi_url="/openapi.json",
    docs_url="/docs",
    lifespan=None,
)
```

主なmethod:

- `route(rule, *, methods=None, endpoint=None, response_model=None, status_code=None, ...)`
- `get`、`post`、`put`、`patch`、`delete`: `route`のshortcut
- `add_url_rule(rule, *, endpoint, view_func, methods=None, ...)`
- `websocket(rule, *, endpoint=None, allowed_origins=None)`
- `register_blueprint(blueprint, *, url_prefix=None, name_prefix="")`
- `url_for(endpoint, **values)`
- `before_request`、`after_request`、`middleware`、`errorhandler`
- `on_startup`、`on_shutdown`、`context_processor`
- `add_middleware(middleware, *args, **kwargs)`
- `render_template`、`jsonify`、`make_response`
- `mount_wsgi`、`mount_asgi`
- `openapi()`、`clear_template_cache()`、`init_database()`
- `test_client()`
- `run(host="127.0.0.1", port=8000, *, workers=0, processes=1, ...)`

routeのOpenAPI optionは`summary`、`description`、`tags`、`responses`、
`request_body`、`response_model`、`status_code`、`operation_id`、
`include_in_schema`、`openapi_extra`です。`response_model`は実行時にも出力を検証・変換し、
宣言外fieldを除外します。

`current_app`は処理中の`Higuma`、`request`は処理中の`Request`へのcontext-local proxyです。
request context外で参照すると`RuntimeError`になります。`app.config`は設定dictionary、
`app.state`はlifespanで初期化した共有状態などに使えます。

### `Blueprint`

`Blueprint(name, import_name, *, url_prefix="")`はroute群をまとめます。`route`、HTTP shortcut、
`websocket`を持ち、`register_blueprint()`で登録します。

### `View` / `MethodView`

`View.as_view(name, *class_args, **class_kwargs)`はclassをview functionへ変換します。
`View`では`dispatch_request()`を、`MethodView`では`get()`、`post()`等を実装します。
`MethodView`の許可methodは実装したmethod名から自動導出されます。

## 型付き入力とdependency injection

`typing.Annotated`でsourceと制約を宣言すると、handler実行前に値を取り出し、型へ変換します。

```python
from typing import Annotated

from higuma import Body, Depends, Header, PathParam, QueryParam


def page_size(limit: Annotated[int, QueryParam(ge=1, le=100)] = 20):
    return limit


@app.post("/items/<uuid:item_id>", response_model=ItemOutput, status_code=201)
def create_item(
    item_id: Annotated[UUID, PathParam()],
    payload: Annotated[ItemInput, Body()],
    limit: Annotated[int, Depends(page_size)],
    trace_id: Annotated[str | None, Header("x-trace-id")] = None,
):
    ...
```

- `QueryParam`、`Header`、`Cookie`、`PathParam`、`Body`、`Form`、`File`は`Parameter`の派生型です。
- 共通optionは`alias`、`title`、`description`、`deprecated`、`min_length`、
  `max_length`、`pattern`、`ge`、`gt`、`le`、`lt`です。
- primitive、`UUID`、`date` / `datetime`、`Enum`、`Literal`、union / optional、
  collection、dataclass、`TypedDict`を再帰的に変換します。
- list等にはrepeated query/form/file値を渡します。
- `Depends(callable, use_cache=True)`は同期・非同期・yield dependencyを再帰解決します。
  yield dependencyのcleanupはresponse bodyとbackground taskの完了後に逆順で実行されます。
- `app.dependency_overrides[dependency] = replacement`でtest時に差し替えられます。
- annotationなしのpath引数、`Request`、`BackgroundTasks`も自動注入できます。

入力不正は`RequestValidationError`としてJSON 422になります。error detailには`loc`、`msg`、
`type`、安全な場合だけ`input`が含まれます。password、token、secret、Cookie等の値はredactします。
出力が`response_model`に適合しない場合は`ResponseValidationError`となり、clientには500を返します。

## Request

### `Request`

- `method`、`path`、`query_string`、`scheme`、`host`、`base_url`、`url`
- `args` / `query`: repeated keyを保持する`MultiDict`
- `headers`: case-insensitiveな`Headers`
- `cookies`: read-only mapping
- `json` / `get_json(force=False, silent=False)`
- `form`、`files`、`body`、`text`、`get_data(as_text=False)`
- `path_params` / `view_args`
- `client_addr` / `remote_addr`
- `state`、`session`、`user`
- `raw_headers`: 重複を保持するbyte header pair

### `Headers` / `MultiDict`

`Headers`はheader名をcase-insensitiveに扱います。`MultiDict`は`get()`、`getlist()`、
`items(multi=False)`、`keys()`、`values()`、`to_dict(flat=True)`を提供します。

### `UploadFile`

`filename`、`content_type`、`headers`、`field_name`、`size`を持ち、`read()`、`seek()`、
`getvalue()`、`save(destination)`を使えます。`secure_filename(filename, fallback="upload")`は
directory要素や危険な文字を除去します。一意名、拡張子、内容、quotaの検査はapplication側の責務です。

## Response

### 基本response

- `Response(body=b"", status=200, headers=None, media_type=None, background=None)`
- `HTMLResponse`、`PlainTextResponse`、`JSONResponse`
- `RedirectResponse(location, status=302, ...)`
- `TemplateResponse(template, context=None, ...)`
- `FileResponse(path, *, filename=None, as_attachment=False, offset=0, length=None, ...)`

full-fileの`FileResponse`と`send_file()`はGET/HEADのETag、`If-None-Match`、single byte rangeを
自動処理します。明示的な`offset` / `length`は指定範囲をそのままRustからstreamします。

`Response`は`status_code` / `status`、`headers`、`media_type`、`body`、`history`を持ちます。
`append_header()`、`get_json()`、`set_cookie()`、`delete_cookie()`を提供します。
`set_cookie(..., partitioned=True)`はPython 3.14以降かつ`secure=True`で利用できます。

helperは`jsonify()`、`make_response()`、`redirect()`、`render_template()`、`send_file()`です。
handlerはresponseに加え、文字列、bytes-like、JSON化可能値、`None`、
`(body, status[, headers])`を返せます。dataclass、enum、UUID、日付等もJSONへ変換されます。

### Streaming / SSE

`StreamingResponse(content, *, media_type=..., content_length=None, background=None)`は同期・非同期
iterableから1 chunkずつ送ります。chunkは`str`またはbytes-likeです。作成時のrequest contextは
stream完了まで保持されます。既知の場合だけ`content_length`を指定してください。

`EventSourceResponse(content, ...)`はSSE responseです。値を直接渡すか、
`ServerSentEvent(data, event=None, id=None, retry=None, comment=None)`でfieldを指定します。
SSEには`text/event-stream`、`Cache-Control: no-cache`、buffering抑止headerが自動設定され、
gzip対象から除外されます。

### Background task

`BackgroundTask(func, *args, **kwargs)`はresponse body送信後に同期・非同期callableを実行します。
`BackgroundTasks`は`add_task()`で複数登録でき、handler引数へも自動注入できます。登録順に実行し、
task登録時のrequest contextを保持します。失敗後のtaskは実行されません。重い処理、再試行や永続性が
必要な処理にはjob queueを使ってください。

## Lifecycle、middleware、session

`Higuma(..., lifespan=context_manager)`はworker開始時にenterし、停止時にexitします。同期・非同期
context managerを使え、yieldしたmappingは`app.state`へmergeされます。従来の`on_startup` /
`on_shutdown`も同じlifecycle内で実行されます。testでは`with app.test_client() as client:`を使うと
lifecycleを実行します。

- `SessionMiddleware(secret_key, *, cookie_name="higuma_session", max_age=..., secure=False, ...)`
- `Session`: dict互換で`permanent`、`modified`を管理
- `CORSMiddleware`
- `SecurityHeadersMiddleware`
- `TrustedHostMiddleware`
- `ProxyHeadersMiddleware(trusted_proxies=("127.0.0.1", "::1"))`
- `RateLimitMiddleware(limit=100, window=60.0, *, key=None, max_keys=10000)`

proxy headerは直接接続元が`trusted_proxies`に含まれる場合だけ信頼してください。

## WebSocket

`WebSocket`は`send()`、`send_text()`、`send_bytes()`、`send_json()`、`receive()`、
`receive_text()`、`receive_bytes()`、`receive_json()`、`close()`を提供します。切断は
`WebSocketDisconnect(code=1000, reason="")`です。routeの`allowed_origins`を省略すると
same-originだけを許可します。

## SQLite ORM

- `Database(url="sqlite:///higuma.db")`: `connect()`、`create_all()`、`drop_all()`、`session()`
- `Model(**values)`: `to_dict()`
- `DatabaseSession`: `add()`、`save()`、`delete()`、`query()`、`execute()`、`commit()`、`rollback()`
- `Query`: `get()`、`filter_by()`、`order_by()`、`offset()`、`limit()`、`first()`、`all()`、
  `count()`、`delete()`、`delete_all()`
- field: `Field`、`Integer`、`Float`、`String`、`Boolean`、`Date`、`DateTime`、`Blob`

fieldの共通optionは`primary_key`、`nullable`、`unique`、`default`、`index`です。
`Integer`は`autoincrement`、`String`は`length`も受けます。`Query.delete()`はfilterなしの全件削除を
拒否します。組み込みORMはSQLite用の小さな同期data mapperです。

## 認証とsecurity

- `AuthManager`: `load_user()`、`login_user()`、`logout_user()`、`confirm_login()`と認証decorator
- `current_user` / `AnonymousUser`
- `login_required`、`fresh_login_required`、`roles_required`、`permissions_required`
- `PasswordHasher`: scryptの`hash()`、`verify()`、`needs_rehash()`
- `TokenSigner`: `dumps()`、`loads(max_age=None)`
- `CSRFProtection` / `csrf_token(field_name=None)`
- `OAuth2Client`: `google()`、`line()`、`discord()`、`authorization_url()`、`fetch_token()`、
  `userinfo()`、`validate_state()`。state、PKCE、OIDC nonceをsessionで管理
- `generate_user_id(prefix="usr", entropy_bytes=18)` / `validate_user_id()`

## 例外と運用

`abort(status_code, detail=None, headers=None)`または次の`HTTPException`派生型をraiseできます。

`BadRequest`、`Unauthorized`、`Forbidden`、`NotFound`、`MethodNotAllowed`、`Conflict`、
`RequestEntityTooLarge`、`UnsupportedMediaType`、`RangeNotSatisfiable`、`TooManyRequests`、
`InternalServerError`。

`Supervisor(app, *, host="127.0.0.1", port=8000, processes=2, ...)`は複数worker process、
health check、restart上限を管理します。通常は`app.run(processes=..., app_ref="module:app")`または
CLIから利用します。

実行コードは[Examples](examples.md)と
[リポジトリのexamples](https://github.com/Madoa5561/higuma/tree/main/examples)を参照してください。
