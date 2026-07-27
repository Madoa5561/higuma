# higuma

[![CI](https://github.com/Madoa5561/higuma/actions/workflows/ci.yml/badge.svg)](https://github.com/Madoa5561/higuma/actions/workflows/ci.yml)
[![Documentation](https://img.shields.io/badge/docs-higuma.moyashi.xyz-0f766e)](https://higuma.moyashi.xyz)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776ab)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-eab308)](LICENSE)

**公式ドキュメント: [higuma.moyashi.xyz](https://higuma.moyashi.xyz)**

Rust 製 HTTP コアと Flask に近い Python API を組み合わせた、SSR 対応の軽量 Web
フレームワークです。

```python
from higuma import Higuma

app = Higuma(__name__)


@app.get("/")
def index():
    return app.render_template("index.html", title="higuma")


@app.get("/users/<int:user_id>")
def user(user_id: int):
    return app.jsonify(user_id=user_id)


if __name__ == "__main__":
    app.run()
```

## プロジェクトの状態

現在のバージョンは `0.1.0` です。Web アプリケーションを作るための主要機能は揃っていますが、
公開直後の API であり、第三者によるセキュリティ監査や大規模な本番実績はまだありません。
本番導入時は「本番運用」の項目も確認してください。

higuma は WSGI/ASGI アダプターではなく、axum と Tokio を利用する HTTP サーバーを
自身で起動します。

## 特徴

- Rust の axum/Tokio による HTTP 受付、ルート照合、リクエスト制限
- Flask に近い `@app.get()`、`@app.post()`、`Blueprint` API
- `<int:id>`、`<uuid:id>`、`<path:name>` を含む動的ルーティング
- `Request`、`Response`、JSON、redirect、ファイルレスポンス
- MiniJinja を Rust 側で描画する SSR
- before/after hooks、独自 middleware、エラーハンドラ
- ETag、条件付き GET、Range に対応した静的ファイル
- 署名付き Cookie セッション
- CORS、セキュリティヘッダ、Trusted Host middleware
- `async def` ハンドラと非同期 lifecycle hooks
- 外部サーバー不要のテストクライアント
- アプリ起動とルート一覧を扱う CLI
- Python 3.10 以降向けの型情報

## アーキテクチャ

```text
Client
  |
  v
axum / Tokio (Rust)
  |  HTTP、ルーティング、HEAD/OPTIONS、body size limit
  v
Python request pipeline
  |  middleware -> before_request -> view -> after_request
  v
Response / TemplateResponse / FileResponse
  |
  v
Rust response encoder / MiniJinja
```

ネットワーク処理やルート照合は Rust で行います。Python の view function を実行する部分は
CPython の GIL の影響を受けるため、すべての処理が Rust だけで完結するわけではありません。

## 必要環境

- Python 3.10 以上
- Rust stable
- Rust が利用できる C/C++ リンカー

Windows では Visual Studio Build Tools の MSVC、Linux では GCC/Clang、macOS では
Xcode Command Line Tools が利用できます。

## インストール

リポジトリから開発用にインストールします。

```bash
git clone https://github.com/Madoa5561/higuma.git
cd higuma
python -m pip install -U pip
python -m pip install -e .
```

開発ツールも含める場合:

```bash
python -m pip install -e ".[dev]"
```

wheel を作る場合:

```bash
python -m pip install maturin
maturin build --release
```

生成物は `target/wheels/` に出力されます。

## クイックスタート

`app.py`:

```python
from higuma import Higuma

app = Higuma(__name__)


@app.get("/")
def index():
    return "<h1>Hello from higuma</h1>"


@app.post("/api/echo")
def echo(request):
    return app.jsonify(received=request.json)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
```

起動:

```bash
python app.py
```

CLI でも起動できます。

```bash
higuma run app:app --host 127.0.0.1 --port 8000
```

ブラウザで `http://127.0.0.1:8000/` を開きます。

## ルーティング

```python
@app.route("/items", methods=["GET", "POST"])
def items(request):
    ...


@app.get("/items/<int:item_id>")
def item(item_id: int):
    return {"item_id": item_id}


@app.put("/items/<int:item_id>")
def update_item(request, item_id: int):
    return {"item_id": item_id, "payload": request.json}
```

利用できる converter:

| Converter | 例 | Python 型 |
| --- | --- | --- |
| `string` / `str` | `<string:name>`、`<name>` | `str` |
| `int` | `<int:item_id>` | `int` |
| `float` | `<float:price>` | `float` |
| `uuid` | `<uuid:object_id>` | `uuid.UUID` |
| `path` | `<path:filename>` | `/` を含む `str` |

`path` converter はルートの最後にだけ配置できます。末尾 `/` の有無は同じルートとして
扱われます。

GET ルートには HEAD が自動で割り当てられ、OPTIONS は `Allow` ヘッダとともに自動応答します。

### URL の生成

```python
@app.get("/users/<int:user_id>")
def profile(user_id: int):
    ...


url = app.url_for("profile", user_id=42)
# /users/42
```

## Request

ハンドラは引数なしでも、`request` 引数付きでも定義できます。

```python
@app.post("/upload/<path:name>")
def upload(request, name: str):
    print(request.method)
    print(request.path)
    print(request.args)
    print(request.headers)
    print(request.body)
    print(request.text)
    print(request.path_params)
```

Flask に近いグローバル proxy も利用できます。

```python
from higuma import request


@app.get("/search")
def search():
    return {"query": request.args.get("q", "")}
```

request context の外で `request` にアクセスすると `RuntimeError` になります。

### Query string

`request.args` は `MultiDict` です。

```python
# /search?tag=rust&tag=python&page=2
tags = request.args.getlist("tag")
page = request.args.get("page", 1, type=int)
```

主なメソッド:

- `get(key, default=None, type=None)`
- `getlist(key, type=None)`
- `items(multi=False)`
- `to_dict(flat=True)`

### JSON

```python
@app.post("/api/items")
def create_item(request):
    payload = request.json
    return {"name": payload["name"]}, 201
```

`Content-Type` が JSON ではない場合は `415 Unsupported Media Type`、不正な JSON は
`400 Bad Request` になります。判定を緩める場合は
`request.get_json(force=True, silent=True)` を利用できます。

### Form と Cookie

```python
name = request.form.get("name")
session_id = request.cookies.get("session_id")
```

`application/x-www-form-urlencoded` に対応しています。multipart form-data は現時点では
未対応です。

## Response

view function は次の値を返せます。

```python
return "HTML string"
return b"binary"
return {"ok": True}
return ["one", "two"]
return "created", 201
return {"ok": True}, 201, {"x-request-id": "abc"}
return Response("custom", status=202)
```

利用できる response class:

- `Response`
- `HTMLResponse`
- `PlainTextResponse`
- `JSONResponse`
- `RedirectResponse`
- `FileResponse`
- `TemplateResponse`

### JSON

```python
from higuma import jsonify

return jsonify({"items": items})
return app.jsonify(items=items, total=len(items))
```

### Redirect

```python
from higuma import redirect

return redirect("/login")
return redirect("/new-location", status=308)
```

### Cookie

```python
response = app.make_response("ok")
response.set_cookie(
    "token",
    "value",
    secure=True,
    httponly=True,
    samesite="Lax",
)
return response
```

削除:

```python
response.delete_cookie("token")
```

### ファイル送信

```python
from higuma import send_file

return send_file("reports/monthly.pdf")
return send_file(
    "exports/data.csv",
    as_attachment=True,
    download_name="data.csv",
)
```

ユーザー入力からファイルパスを直接組み立てないでください。公開ディレクトリ配下を安全に配信する
場合は組み込みの static route を利用します。

## SSR とテンプレート

higuma は MiniJinja を使い、テンプレートを Rust 側で描画します。基本構文は Jinja2 に近い
ものです。

`templates/index.html`:

```html
<!doctype html>
<html lang="ja">
  <head>
    <meta charset="utf-8">
    <title>{{ title }}</title>
  </head>
  <body>
    <h1>{{ heading }}</h1>
    {% for item in items %}
      <p>{{ item }}</p>
    {% endfor %}
  </body>
</html>
```

Python:

```python
app = Higuma(__name__, template_folder="templates")


@app.get("/")
def index():
    return app.render_template(
        "index.html",
        title="higuma",
        heading="Rust powered SSR",
        items=["fast", "small", "typed"],
    )
```

全テンプレートに値を追加する context processor:

```python
@app.context_processor
def shared_context():
    return {"app_name": "higuma"}
```

グローバルの `render_template()` も利用できますが、context processor を適用する場合は
`app.render_template()` を利用してください。

## 静的ファイル

デフォルトではアプリケーションルートの `static/` が `/static/` に割り当てられます。

```python
app = Higuma(
    __name__,
    static_folder="public",
    static_url_path="/assets",
)
```

組み込み static route は次に対応します。

- MIME type の推測
- path traversal の防止
- ETag と `If-None-Match`
- `Cache-Control`
- 単一 byte range
- `Last-Modified`

cache 時間:

```python
app.config["STATIC_CACHE_MAX_AGE"] = 86400
```

静的配信を無効にする場合:

```python
app = Higuma(__name__, static_folder=None)
```

大規模な本番配信では CDN またはリバースプロキシから配信することを推奨します。

## エラー処理

```python
from higuma import NotFound, abort


@app.get("/admin")
def admin():
    abort(403, "administrator only")


@app.errorhandler(403)
def handle_forbidden(error):
    return {"error": error.detail}, 403


@app.errorhandler(NotFound)
def handle_not_found(error):
    return app.render_template("404.html", path=request.path), 404
```

組み込み例外:

- `BadRequest` (`400`)
- `Unauthorized` (`401`)
- `Forbidden` (`403`)
- `NotFound` (`404`)
- `MethodNotAllowed` (`405`)
- `Conflict` (`409`)
- `RequestEntityTooLarge` (`413`)
- `UnsupportedMediaType` (`415`)
- `TooManyRequests` (`429`)
- `InternalServerError` (`500`)

`debug=True` では未処理例外の traceback がレスポンスに含まれます。本番では有効にしないで
ください。

## Hooks と middleware

### before / after request

```python
@app.before_request
def authenticate(request):
    if request.path.startswith("/admin"):
        ...


@app.after_request
def add_request_id(request, response):
    response.headers["x-request-id"] = request.state["request_id"]
    return response
```

before hook が response を返すと view function は実行されません。

### 独自 middleware

```python
import time


@app.middleware
def timing(request, call_next):
    started = time.perf_counter()
    response = app.make_response(call_next(request))
    response.headers["server-timing"] = (
        f"app;dur={(time.perf_counter() - started) * 1000:.2f}"
    )
    return response
```

後から登録した middleware ほど内側で実行されます。

### CORS

```python
from higuma import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://example.com"],
    allow_methods=["GET", "POST"],
    allow_headers=["content-type", "authorization"],
    allow_credentials=True,
)
```

credential を許可する場合、origin に `*` を使わないでください。

### セキュリティヘッダ

```python
from higuma import SecurityHeadersMiddleware

app.add_middleware(
    SecurityHeadersMiddleware,
    content_security_policy="default-src 'self'; img-src 'self' data:",
)
```

### Trusted Host

```python
from higuma import TrustedHostMiddleware

app.add_middleware(
    TrustedHostMiddleware,
    ["example.com", "*.example.com"],
)
```

## 署名付きセッション

```python
from higuma import SessionMiddleware

app.add_middleware(
    SessionMiddleware,
    secret_key="replace-with-a-long-random-secret",
    secure=True,
)


@app.get("/counter")
def counter(request):
    request.session["count"] = request.session.get("count", 0) + 1
    return {"count": request.session["count"]}
```

セッションデータは署名されますが暗号化はされません。パスワード、token、個人情報を保存しないで
ください。Cookie サイズを超える大量のデータにも適していません。

## Blueprint

```python
from higuma import Blueprint

api = Blueprint("api", __name__, url_prefix="/api")


@api.get("/health")
def health():
    return {"status": "ok"}


app.register_blueprint(api)
```

endpoint は `api.health` になります。

```python
app.url_for("api.health")
```

## 非同期ハンドラ

```python
@app.get("/remote")
async def remote_data():
    result = await some_async_operation()
    return {"result": result}
```

`async def` は利用できますが、現在の Rust/Python bridge は Python coroutine を同期 boundary
で待機します。大量の長時間接続を扱う純粋 ASGI フレームワークと同じ実行モデルではありません。
CPU-bound 処理は process pool や job queue に分離してください。

## Lifecycle

```python
@app.on_startup
async def connect():
    ...


@app.on_shutdown
async def disconnect():
    ...
```

## Config

```python
app.config.from_mapping(
    DEBUG=False,
    STATIC_CACHE_MAX_AGE=86400,
)

app.config.from_object("settings.ProductionConfig")
app.config.from_file("config.json")
app.config.from_envvar("HIGUMA_CONFIG")
app.config.from_prefixed_env("HIGUMA")
```

`from_prefixed_env()` は `HIGUMA_DEBUG=false` のような値を JSON として解釈できる場合は
JSON 型に変換します。

主な初期値:

| Key | Default |
| --- | --- |
| `DEBUG` | `False` |
| `TESTING` | `False` |
| `MAX_CONTENT_LENGTH` | `8 * 1024 * 1024` |
| `STATIC_CACHE_MAX_AGE` | `3600` |
| `SERVER_HEADER` | `higuma/<version>` |

`MAX_CONTENT_LENGTH` と `SERVER_HEADER` は Rust core 作成時に固定されるため、constructor の
`max_content_length` を使って設定してください。

## CLI

アプリ起動:

```bash
higuma run app:app
higuma run package:create_app --host 0.0.0.0 --port 8000 --workers 4
```

factory function は引数なしで呼び出されます。

ルート一覧:

```bash
higuma routes app:app
```

バージョン:

```bash
higuma --version
```

`HIGUMA_APP=app:app` を設定すると CLI の app 引数を省略できます。

## テスト

```python
def test_health():
    client = app.test_client()
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json == {"status": "ok"}
```

利用できるメソッド:

- `client.get()`
- `client.post()`
- `client.put()`
- `client.patch()`
- `client.delete()`
- `client.head()`
- `client.options()`
- `client.open()`

JSON、form、query:

```python
client.post("/items", json={"name": "book"})
client.post("/login", data={"name": "higuma"})
client.get("/search", query={"q": "rust"})
```

セッション Cookie は同じ client 内で保持されます。

プロジェクト自身のテスト:

```bash
python -m unittest discover -s tests -v
```

## 本番運用

最低限、次を行ってください。

- `debug=False` にする
- TLS 終端、接続制限、request buffering を行うリバースプロキシを前段に置く
- `TrustedHostMiddleware` を設定する
- CORS と Content Security Policy をアプリごとに見直す
- 強い session secret と HTTPS 用の secure Cookie を使う
- static file は可能なら CDN へ分離する
- timeout、ログ収集、監視、process restart を外部 supervisor で管理する

`workers` は Tokio worker thread 数であり、Python process 数ではありません。複数 CPU core で
Python view を並列化する場合は、OS の process manager または container replicas を使い、
異なる port/socket に複数 process を配置してください。

## パフォーマンス

ベンチマーク:

```bash
python -m pip install flask
python bench/compare.py
```

この script はローカルの単純な `GET /ping` を比較する開発用 smoke benchmark です。
Flask 開発サーバーとの比較は本番構成同士の公平な比較ではなく、結果は CPU、OS、Python、
client、handler 内容で大きく変化します。

性能を評価するときは、自分の application handler、同じ TLS/proxy 構成、同じ worker 数、
latency percentile、error rate を含めて測定してください。

higuma が高速化する主な範囲:

- HTTP connection と body の処理
- ルート照合
- SSR template rendering
- HEAD/OPTIONS と一部 static response

Python handler、database、外部 API、serialization が支配的な workload では改善幅は小さく
なります。

## 現在の制約

`0.1.0` では次は未対応です。

- WebSocket
- multipart file upload
- WSGI / ASGI mount
- OpenAPI 自動生成
- template bytecode cache
- built-in database / ORM
- built-in multi-process supervisor

これらはアプリケーションごとに外部ライブラリで補うか、今後のバージョンで追加します。

## プロジェクト構成

```text
higuma/
├── src/lib.rs                 # Rust HTTP core
├── python/higuma/             # Python public API
├── tests/                     # integration tests
├── examples/                  # runnable SSR example
├── bench/                     # local benchmark harness
├── .github/workflows/         # CI and wheel builds
├── pyproject.toml             # Python package metadata
└── Cargo.toml                 # Rust crate metadata
```

## 開発

```bash
python -m pip install -e ".[dev]"
cargo fmt --all -- --check
cargo check
python -m ruff check python tests examples
python -m unittest discover -s tests -v
```

詳しくは `CONTRIBUTING.md`、セキュリティ報告は `SECURITY.md`、変更履歴は
`CHANGELOG.md` を参照してください。

## License

MIT License
