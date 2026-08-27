# higuma

**Pythonで書き、Rustで配信するWebフレームワーク。**

[English documentation](en/index.md){ .md-button }
[5分ではじめる](getting-started.md){ .md-button .md-button--primary }

```bash
python -m pip install -U higuma
```

```python
from higuma import Higuma

app = Higuma(__name__)


@app.get("/")
def index():
    return {"message": "Hello from higuma"}


app.run()
```

## 設計

higumaは、Flaskに着想を得たdecorator APIと、axum / Tokioによる組み込みHTTP
serverを組み合わせます。application logicはPythonで記述し、socket、route matching、
file streaming、gzip、WebSocketをRust coreが処理します。

higumaはFlaskのdrop-in replacementでも、ASGI serverへ渡すASGI applicationでも
ありません。既存のWSGI / ASGI applicationはroute配下へmountできます。

## 0.4.0の機能

- `Annotated`型付き入力、dependency injection、response model、構造化422
- typed converter、自動HEAD/OPTIONS、sync / async handler、class-based view
- Blueprint、middleware、request / response hook、lifespan
- MiniJinja SSR、context processor、compiled template cache
- sync / async streaming、SSE、background task、range / ETag file、automatic gzip
- text / binary / JSON WebSocket、same-origin検証、認証preflight
- multipart uploadと安全なfilename helper
- OpenAPI 3.1 / Swagger UI、dataclass・TypedDict・enum等のschema
- transactionとpaginationを備えたSQLite ORM
- signed session、login、role / permission、OAuth 2.0 + PKCE
- password、token、CSRF、CORS、rate limit、trusted host / proxy
- in-process test client、CLI、multi-process supervisor

!!! important "OpenAPIと入力検証"
    `Annotated[..., Body()]`等はruntimeで入力を検証・Python objectへ変換し、同じ宣言から
    OpenAPIを生成します。`response_model`は出力も検証します。従来の`request_body=`と
    return annotation単独はschema metadataです。

## 対応範囲

- CPython 3.10以降
- Windows、Linux、macOS向けABI3 wheel
- 組み込みORMはSQLite専用
- TLS終端、buffering、network-level limitはreverse proxyを推奨

## 次に読む

- [はじめる](getting-started.md)
- [機能ガイド](features.md)
- [認証とセキュリティ](security-auth.md)
- [Examples](examples.md)
- [APIリファレンス](api.md)
- [デプロイ](deployment.md)

!!! warning
    higumaは活発に開発中です。本番採用前に、対象applicationで負荷試験、障害試験、
    security reviewを行ってください。
