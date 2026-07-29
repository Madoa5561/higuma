# はじめる

## 必要環境

- Python 3.10以上
- Windows、Linux、macOS

```bash
python -m pip install -U higuma
```

`app.py`を作成します。

```python
from higuma import Higuma, request

app = Higuma(__name__)


@app.get("/")
def index():
    return "<h1>Hello higuma</h1>"


@app.get("/users/<int:user_id>")
def user(user_id: int):
    return {"user_id": user_id}


@app.post("/echo")
def echo():
    return {"received": request.json}


if __name__ == "__main__":
    app.run()
```

```bash
python app.py
```

- アプリ: <http://127.0.0.1:8000>
- Swagger UI: <http://127.0.0.1:8000/docs>
- OpenAPI: <http://127.0.0.1:8000/openapi.json>

## CLI

```bash
higuma run app:app --host 127.0.0.1 --port 8000
higuma routes app:app
higuma run app:app --processes 4
```

## レスポンス

```python
@app.get("/text")
def text():
    return "HTML or text"


@app.get("/json")
def json_response():
    return {"ok": True}, 200, {"x-example": "higuma"}
```

## 0.2から0.3への移行

- `SessionMiddleware`、`AuthManager`、`TokenSigner`のsecretを32バイト以上にする
- 意図した全件削除を`query.delete()`から`query.delete_all()`へ変更する
- cross-origin WebSocketは`allowed_origins=(...)`を明示する
- reverse proxyのheaderを使う場合は`ProxyHeadersMiddleware`を追加する
