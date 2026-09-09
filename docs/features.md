# 機能ガイド

## Routingとhandler

```python
@app.route("/items/<int:item_id>", methods=("GET", "POST"))
def item(item_id: int):
    return {"item_id": item_id, "method": request.method}
```

`get`、`post`、`put`、`patch`、`delete`は`route`のshortcutです。固定pathは
動的pathより優先されます。同じpathとmethod、または同じendpointを重複登録すると
`ValueError`になります。

handlerは同期関数と`async def`の両方を受け付けます。path parameterは同名の引数へ、
`request`という名前の引数には`Request`を渡します。通常は`from higuma import request`
のproxyを使うとsignatureが明確です。

```python
@app.get("/wait")
async def wait():
    await some_async_operation()
    return {"done": True}
```

converterは`string`、`int`、`float`、`uuid`、`path`を利用できます。

class単位でviewをまとめる場合は`View`または`MethodView`を使えます。`MethodView`は
実装した`get()`、`post()`等から許可methodを自動判定します。

## 型付き入力とdependency

```python
from typing import Annotated

from higuma import Body, Depends, Header, PathParam, QueryParam


def pagination(limit: Annotated[int, QueryParam(ge=1, le=100)] = 20):
    return limit


@app.post("/items/<uuid:item_id>", response_model=ItemOutput, status_code=201)
def create_item(
    item_id: Annotated[UUID, PathParam()],
    payload: Annotated[ItemInput, Body()],
    limit: Annotated[int, Depends(pagination)],
    trace_id: Annotated[str | None, Header("x-trace-id")] = None,
):
    return {"id": item_id, **payload.__dict__}
```

`QueryParam`、`Header`、`Cookie`、`PathParam`、`Body`、`Form`、`File`は入力元と制約を
宣言します。dataclass、TypedDict、UUID、date、datetime、Enum、Literal、collection、
optionalを再帰的に変換し、不正な入力は構造化されたJSON 422になります。

`Depends`はnested dependency、同期・非同期callable、yield cleanup、request内cacheに対応します。
testでは`app.dependency_overrides`で差し替えられます。`response_model`は出力を実行時に検証・変換し、
宣言外fieldを除外します。

dataclassインスタンスを返す場合も再検証し、サブクラスだけが持つfieldは出力しません。
この処理はcollectionやTypedDict内のモデルにも適用され、元のインスタンスは変更しません。
dataclassのfieldなどに付けた `Annotated` の制約も、入力・出力の両方で検証します。

## Request data

```python
@app.post("/search")
def search():
    tags = request.args.getlist("tag")
    payload = request.get_json(silent=True)
    return {
        "tags": tags,
        "payload": payload,
        "client": request.remote_addr,
    }
```

- `args` / `query`: repeated keyを保持する`MultiDict`
- `headers`: case-insensitiveな`Headers`
- `json` / `get_json()`: JSON body。media typeが不正なら`UnsupportedMediaType`
- `form` / `files`: URL-encodedまたはmultipart form
- `body` / `get_data()` / `text`: raw body
- `cookies`: read-only mapping
- `path_params` / `view_args`: converter適用済みpath parameter
- `client_addr` / `remote_addr`: 直接peer。trusted proxy middlewareでのみ更新
- `state`: middlewareとhandlerで共有するrequest-local dictionary
- `session` / `user`: 対応middlewareが設定

`raw_headers`は重複を保持したbyte pair列で、WSGI / ASGI相互運用向けです。

## Response

handlerは`str`、bytes-like、`dict`、`list`、`None`、`Response`、または
`(body, status[, headers])`を返せます。

```python
@app.post("/items")
def create_item():
    response = app.jsonify({"id": 1, "status": "created"}, status=201)
    response.set_cookie("notice", "created", httponly=True, samesite="Lax")
    return response
```

`HTMLResponse`、`PlainTextResponse`、`JSONResponse`、`RedirectResponse`、
`FileResponse`、`TemplateResponse`を明示的に選べます。header name/valueとstatusは
response生成時およびmiddleware変更後に検証されます。`Content-Length`は手動設定せず、
Rust coreに任せてください。

`StreamingResponse`は同期・非同期iterableを全量bufferせず送ります。作成時のrequest contextは
反復完了まで保持されます。`EventSourceResponse`と`ServerSentEvent`はSSEのfield整形、no-cache、
proxy buffering抑止を行います。`BackgroundTask` / `BackgroundTasks`はbody送信後に軽量な同期・非同期
処理を実行します。永続性や再試行が必要なら外部job queueを使ってください。

## BlueprintとURL生成

```python
api = Blueprint("api", __name__, url_prefix="/api")


@api.get("/users/<int:user_id>")
def user(user_id: int):
    return {"id": user_id}


app.register_blueprint(api)
profile_url = app.url_for("api.user", user_id=42)
```

登録時の`url_prefix`でBlueprintの値を上書きでき、`name_prefix`でendpoint namespaceを
追加できます。BlueprintはHTTP routeとWebSocket routeに対応します。

## Hooks、middleware、lifecycle

```python
@app.before_request
def start_timer():
    request.state["started"] = time.monotonic()


@app.after_request
def add_timing(response):
    elapsed = time.monotonic() - request.state["started"]
    response.headers["server-timing"] = f"app;dur={elapsed * 1000:.2f}"
    return response


@app.errorhandler(404)
def not_found(error):
    return {"error": error.detail}, 404
```

`before_request`は登録順、`after_request`は逆順です。before hookがresponseを返すと
handlerをskipします。middlewareは最初に追加したものがrequest側で最外層となり、
response側では最後に戻ります。sessionを必要とするmiddlewareは`SessionMiddleware`
より後の内側で動くよう登録してください。

`Higuma(..., lifespan=context_manager)`は単一workerごとに同期・非同期context managerをenter / exitし、
yieldしたmappingを`app.state`へmergeします。`on_startup` / `on_shutdown`も同じlifecycleで実行され、
同期・非同期関数を使えます。`context_processor`のmappingは`app.render_template()`のcontextへmerge
されます。loop-bound async clientを共有する場合は、そのclientを作成したevent loopとの整合性を
application側で保ってください。

## Template、static file、download

```python
@app.context_processor
def globals_for_templates():
    return {"site_name": "higuma example"}


@app.get("/")
def page():
    return app.render_template("index.html", title="Home")
```

MiniJinja environmentとcompiled templateはRust側で共有されます。開発中に強制再読込する
場合は`app.clear_template_cache()`を呼びます。

`static_folder`が有効なら`static_url_path`配下でfileを配信します。static response、
`send_file()`、full-fileの`FileResponse`はETag、`If-None-Match`、GET/HEADのsingle byte rangeに
対応します。file全体やrangeをPython memoryへ読み込まずRustからseek / streamします。

## Multipart upload

```python
from pathlib import Path
from secrets import token_hex


@app.post("/upload")
def upload():
    uploaded = request.files["file"]
    destination = Path("uploads") / f"{token_hex(8)}-{uploaded.filename}"
    uploaded.save(destination)
    return {"name": destination.name, "size": uploaded.size}, 201
```

`UploadFile.filename`は`secure_filename()`で正規化済みですが、同名上書きを防ぐ一意名、
許可extension / media type、保存quota、malware scanはapplication側で実装します。
request全体の上限は`Higuma(max_content_length=...)`です。

## WebSocket

```python
@app.websocket(
    "/ws/<string:room>",
    allowed_origins=("https://example.com",),
)
def chat(ws, room):
    while True:
        ws.send_json({"room": room, "message": ws.receive_json()})
```

Origin未指定時はsame-originのみ許可されます。送受信queueはboundedで、message sizeは
`max_content_length`に従います。認証decoratorはHTTP 101より前に評価されます。
切断時は`WebSocketDisconnect`を処理してください。

## WSGI / ASGI mount

```python
app.mount_wsgi("/legacy", flask_app, name="legacy")
app.mount_asgi("/service", asgi_app, name="service")
```

mount先にはprefixを除いたpathが渡されます。HTTP mountであり、ASGI WebSocketや
lifespan scopeは転送しません。nameはroute endpointの重複を避けるため一意にします。

## OpenAPI

```python
from dataclasses import dataclass


@dataclass
class CreateUser:
    name: str


@app.post("/users", response_model=CreateUser, status_code=201, tags=("users",))
def create_user(payload: Annotated[CreateUser, Body()]):
    return payload
```

`/openapi.json`と`/docs`は標準で有効です。annotationからprimitive、container、
union / optional / literal、dataclass、TypedDict、enum、UUID、date / datetime等を
OpenAPI 3.1 schemaへ変換します。

`Annotated[..., Body()]`等のparameter markerはruntime validationとOpenAPIの両方に使われます。
`response_model`はruntime output validationにも使われます。一方、従来の`request_body=`と単独の
return annotationはschema metadataとして利用でき、runtime validationを有効にするものではありません。

## SQLite ORM

```python
class User(Model):
    id = Integer(primary_key=True, autoincrement=True)
    email = String(nullable=False, unique=True, index=True)


db = Database("sqlite:///app.db")
db.create_all(User)
with db.session() as session:
    session.add(User(email="bear@example.com"))
```

session blockは成功時commit、例外時rollbackです。`filter_by()`は値をparameter bindingし、
`order_by()`はmodel field名だけを受け付けます。`offset()` / `limit()`でpaginationできます。
filterなしの`delete()`は拒否され、意図的な全件削除だけ`delete_all()`を使います。

組み込みORMはSQLite専用の小さなdata mapperです。relationship、migration、async query、
connection poolを必要とするapplicationでは専門のdatabase libraryを検討してください。

## Test client

```python
client = app.test_client()
response = client.post(
    "/upload",
    data={"caption": "bear"},
    files={"file": ("bear.txt", b"hello", "text/plain")},
)
assert response.status_code == 201
```

clientはcookieをrequest間で保持し、JSON、form、multipart、HEAD/OPTIONS、redirect historyを扱います。
`with app.test_client() as client:`ではlifespanとstartup/shutdown hookも実行します。socketを使う
real network protocolは実行しないため、WebSocket、wire-level framing、proxy、supervisorの最終確認には
real serverを使います。
