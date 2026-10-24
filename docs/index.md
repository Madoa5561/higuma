<div class="brand-hero" markdown="1">

![higuma — 日本のヒグマをモチーフにしたロゴ](assets/higuma-mark.png){ .brand-hero__mark width="144" height="132" }

# higuma

**Pythonで書き、Rustで配信するWebフレームワーク。**

[English documentation](en/index.md){ .md-button }
[5分ではじめる](getting-started.md){ .md-button .md-button--primary }

</div>

API、HTML、リアルタイム通信を、書き慣れたPythonのdecoratorで。
higumaという名前は日本のヒグマに由来します。

!!! info "0.8.0の新機能"
    `app.asgi_app`／`app.wsgi_app`で外部サーバーにも載せられます。
    ASGI HTTP・lifespan・WebSocketと、WSGI HTTPに対応します。
    同期互換worker・stream・proxy・resourceの制約は[サーバーインターフェース](server-interfaces.md)、
    検証方法は[テストガイド](testing.md)を参照してください。

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

higumaはFlaskのdrop-in replacementではありません。外部ASGI／WSGIサーバーへは
明示的な`asgi_app`／`wsgi_app`を渡します。逆方向の、他アプリのHTTP mountも提供します。

## 主な機能

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
- Flask風application context、`g`、session proxy、flash、URL生成
- password、token、CSRF、CORS、rate limit、trusted host / proxy
- in-process test client、CLI、multi-process supervisor
- CSR/SPA history fallback、複数static root、Vite manifest（0.6.0以降）

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
- [CSR・フロントエンド](frontend.md)
- [機能対応表](capabilities.md)
- [Flask互換性](flask-compat.md)
- [ASGI・WSGI・組み込みサーバー](server-interfaces.md)
- [テストとrequest context](testing.md)
- [認証とセキュリティ](security-auth.md)
- [Examples](examples.md)
- [APIリファレンス](api.md)
- [デプロイ](deployment.md)
- [ロゴ・ブランド素材](branding.md)

!!! warning
    higumaは活発に開発中です。本番採用前に、対象applicationで負荷試験、障害試験、
    security reviewを行ってください。
