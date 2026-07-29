# デプロイ

## Multi-process supervisor

```bash
higuma run app:app --host 0.0.0.0 --port 8000 --processes 4 --max-connections 1024
```

`--max-connections`を超えた接続は拒否され、proxy threadは無制限に増加しません。

親プロセスがTCPロードバランサーとして動作し、独立したRust workerへ
接続を振り分けます。HTTPとWebSocketの両方を透過し、異常終了したworkerは
restart limit内で自動再起動します。

## Reverse proxy

本番環境ではCloudflare、Caddy、nginxなどでTLSを終端してください。
`TrustedHostMiddleware`、厳密なCORS origin、secure cookieを設定します。

```python
app.add_middleware(
    TrustedHostMiddleware,
    ("example.com", "*.example.com"),
)
app.add_middleware(
    ProxyHeadersMiddleware,
    trusted_proxies=("127.0.0.1",),
)
```

## Environment

秘密情報は環境変数またはsecret managerで管理します。

```text
HIGUMA_SECRET_KEY=...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
```

## チェックリスト

- HTTPSを強制する
- `debug=False`
- 32バイト以上の推測不能なsecret key
- SQLiteファイルのバックアップ
- uploadサイズと保存先を制限
- reverse proxyが接続元headerを上書きする
- load testと脆弱性診断を実施する
