# はじめる

## 必要環境

- CPython 3.10以上
- Windows、Linux、macOS

```bash
python -m pip install -U higuma
```

配布wheelがないplatformではRust toolchainを使ってsource distributionをbuildします。

## アプリケーションを作る

`app.py`を作成します。

```python
import asyncio

from higuma import Higuma, request

app = Higuma(__name__, max_content_length=8 * 1024 * 1024)


@app.get("/")
def index():
    return "<h1>Hello higuma</h1>"


@app.get("/users/<int:user_id>")
def user(user_id: int):
    return {"user_id": user_id, "verbose": request.args.get("verbose") == "1"}


@app.post("/echo")
def echo():
    data = request.get_json()
    if not isinstance(data, dict):
        return {"error": "JSON object required"}, 400
    return {"received": data}, 201


@app.get("/async")
async def async_handler():
    await asyncio.sleep(0)
    return {"async": True}


if __name__ == "__main__":
    app.run()
```

```bash
python app.py
```

- アプリ: <http://127.0.0.1:8000>
- Swagger UI: <http://127.0.0.1:8000/docs>
- OpenAPI JSON: <http://127.0.0.1:8000/openapi.json>

`dict`と`list`はJSON、`str`はHTMLとして返ります。明示的なresponse classや
`(body, status, headers)` tupleも利用できます。

```python
from higuma import PlainTextResponse


@app.get("/health")
def health():
    return PlainTextResponse("ok", headers={"cache-control": "no-store"})


@app.get("/created")
def created():
    return {"ok": True}, 201, {"x-example": "higuma"}
```

## Route converter

| Syntax | Python value | 一致するpath |
| --- | --- | --- |
| `<string:name>` | `str` | slashを含まないsegment |
| `<int:item_id>` | `int` | 符号付き整数 |
| `<float:value>` | `float` | 小数 |
| `<uuid:item_id>` | `uuid.UUID` | UUID |
| `<path:filename>` | `str` | slashを含む残りのpath |

固定routeは動的routeより優先されます。encoded slash、不正UTF-8、control character、
曖昧なpathは拒否されます。

## CLIとapp factory

```bash
higuma --version
higuma routes app:app
higuma run app:app --host 127.0.0.1 --port 8000
higuma run app:app --processes 4 --max-connections 1024
```

`module:object`のobjectがcallableなら、引数なしのapp factoryとして呼ばれます。

```python
def create_app():
    app = Higuma(__name__)
    return app
```

```bash
higuma run app:create_app
```

複数processではimport可能な`module:object`が必要です。`python app.py`から直接
`processes > 1`を使う場合は`app_ref="app:app"`も指定します。

## 最初のtest

test clientはnetwork socketを開かず、同じdispatch pipelineを通します。

```python
def test_index():
    client = app.test_client()
    response = client.get("/users/42", query={"verbose": "1"})

    assert response.status_code == 200
    assert response.json["user_id"] == 42
```

WebSocket、streaming、supervisor、proxy headerはtest clientだけでは実protocolを
検証できません。該当機能はreal serverでもtestしてください。

## 次に読む

- [機能ガイド](features.md)
- [Examples](examples.md)
- [APIリファレンス](api.md)
- [認証とセキュリティ](security-auth.md)

## 0.3から0.4への移行

- DB query型を直接annotationする場合は`from higuma import Query`を使えるようになりました。
- `higuma.Session`はcookie sessionです。DB session型は`DatabaseSession`です。
- OAuthのstate / PKCE flowには`SessionMiddleware`が必須です。
- custom CSRF fieldを使う場合、`csrf_token()`はactive `CSRFProtection`の設定へ追従します。
- `Annotated`と`QueryParam` / `Body`等でruntime入力検証と変換を有効にできます。
- `response_model`はruntime出力検証とfield filteringを行います。
- 従来の`request_body=`やreturn annotation単独は引き続きschema metadataです。
- `StreamingResponse`、SSE、background task、lifespan、class-based viewが追加されました。
