# デプロイ

higumaはaxum/TokioのHTTP serverを内蔵しています。WSGI/ASGI serverへmountするのではなく、
application自体を起動し、前段のreverse proxyから接続します。

## Production設定

```python title="app.py"
import os

from higuma import (
    Higuma,
    SecurityHeadersMiddleware,
    SessionMiddleware,
    TrustedHostMiddleware,
)

app = Higuma(__name__)
app.add_middleware(TrustedHostMiddleware(["example.com"]))
app.add_middleware(SecurityHeadersMiddleware())
app.add_middleware(
    SessionMiddleware(
        secret_key=os.environ["SESSION_SECRET"],
        secure=True,
    )
)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
```

## Reverse proxy

TLS終端、request size上限、timeout、access log、rate limitはCaddyやnginxなどの前段で設定します。

```caddy title="Caddyfile"
example.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8000
}
```

## チェックリスト

- [ ] 長くrandomなsession secretを環境変数から渡す
- [ ] `secure=True` のcookieを使う
- [ ] `TrustedHostMiddleware` で公開hostを限定する
- [ ] TLSを有効化する
- [ ] body size、timeout、rate limitを要件に合わせる
- [ ] lifecycle hookでDB接続を開閉する
- [ ] production相当の負荷試験を行う
- [ ] process監視と再起動をsystemd、container runtime等へ任せる

## 対応範囲

現在、built-in multi-process supervisor、WebSocket、WSGI/ASGI mountは含まれません。
複数processはOSまたはcontainer orchestratorで管理してください。
