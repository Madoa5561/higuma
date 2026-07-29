# 機能ガイド

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

Origin未指定時はsame-originのみ許可されます。送受信queueはboundedで、
message sizeは`max_content_length`に従います。認証decoratorはHTTP 101より前に評価されます。
`Blueprint.websocket()`も同じAPIです。

## Multipart upload

```python
@app.post("/upload")
def upload():
    file = request.files["file"]
    file.save("uploads")
    return {"name": file.filename, "size": file.size}, 201
```

## WSGI / ASGI mount

```python
app.mount_wsgi("/legacy", flask_app)
app.mount_asgi("/service", asgi_app)
```

mount先にはprefixを除いたpathが渡されます。

## OpenAPI

```python
@app.post(
    "/users",
    summary="Create user",
    tags=("users",),
    request_body=CreateUser,
    responses={"201": {"description": "Created"}},
)
def create_user():
    return request.json, 201
```

`/openapi.json`と`/docs`は標準で有効です。

## Template cache

MiniJinja environmentはRust側で共有され、読み込んだtemplateを再利用します。
開発中に強制再読込する場合は`app.clear_template_cache()`を呼びます。

## ORM

```python
class User(Model):
    id = Integer(primary_key=True, autoincrement=True)
    email = String(nullable=False, unique=True)


db = Database("sqlite:///app.db")
db.create_all(User)
with db.session() as session:
    session.add(User(email="bear@example.com"))
```

session blockは成功時commit、例外時rollbackです。
`query.offset(20).limit(10)`でpaginationできます。filterなしの`delete()`は
事故防止のため拒否され、全件削除は`delete_all()`を明示します。

## File streamingと圧縮

`send_file()`と`FileResponse`はRustからchunk streamingされるため、
大きなfileを全量RAMへ読み込みません。対応clientにはgzipも自動適用されます。
