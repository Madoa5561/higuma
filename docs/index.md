# higuma

**Pythonの書きやすさとRustの実行基盤を組み合わせた、FlaskライクなWebフレームワーク。**

[English documentation](en/index.md){ .md-button }
[5分ではじめる](getting-started.md){ .md-button .md-button--primary }

```bash
pip install higuma
```

```python
from higuma import Higuma

app = Higuma(__name__)


@app.get("/")
def index():
    return {"message": "Hello from higuma"}


app.run()
```

## 0.3.0の主要機能

- axum / Tokioを使用したRust HTTPコア
- Flaskライクなrouting、Blueprint、middleware、hooks
- MiniJinja SSRとコンパイル済みtemplate cache
- 認証preflight、Origin検証、bounded queue付きWebSocket
- multipart file upload
- WSGI / ASGI application mount
- OpenAPI 3.1とSwagger UIの自動生成
- 外部依存なしのSQLite ORM
- worker自動再起動付きmulti-process supervisor
- session認証、password hashing、CSRF、rate limit
- Google、LINE、Discordを含むOAuth 2.0 client
- streaming file responseとgzip
- trusted proxy header、厳格なresponse/session安全検証

## 次に読む

- [機能ガイド](features.md)
- [認証とセキュリティ](security-auth.md)
- [豊富なexamples](examples.md)
- [APIリファレンス](api.md)

!!! note
    higumaは開発中のフレームワークです。本番採用前に負荷試験と
    セキュリティレビューを行ってください。
