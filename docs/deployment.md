# デプロイ

## 構成を選ぶ

単一プロセスは開発や小規模な内部サービス向けです。本番でCPUコアを使う場合は、
Higumaのmulti-process Supervisorを、TLSを終端する信頼済みreverse proxyの後ろに
配置してください。

```text
Internet
  -> trusted reverse proxy (TLS終端、forwarded headerを上書き)
  -> Higuma Supervisor :8000
  -> Rust worker 127.0.0.1:<ephemeral port>
```

```bash
higuma run app:app \
  --host 127.0.0.1 \
  --port 8000 \
  --processes 4 \
  --max-connections 1024
```

SupervisorはTCPロードバランサーとして動作し、HTTPとWebSocket接続を独立した
Rust workerへ振り分けます。異常終了したworkerは、設定したrestart windowと上限の
範囲で再起動します。`--max-connections`を超えた接続は拒否され、proxy threadは
無制限に増えません。

## 接続元IPに関する重要な制約

Supervisorからworkerへの接続はloopbackです。そのため、workerがソケットから見る
直接のpeerは常に`127.0.0.1`（IPv6構成では`::1`）であり、元のclient IPでは
ありません。Supervisor自体は`X-Forwarded-For`を生成しません。

本番では、外側の信頼済みreverse proxyが、クライアントから届いた値を採用せずに
`X-Forwarded-For`と`X-Forwarded-Proto`を**上書き**する構成にしてください。
worker側では、workerへ直接接続するSupervisorのloopbackだけを信頼します。

```python
from higuma import Higuma, ProxyHeadersMiddleware, TrustedHostMiddleware

app = Higuma(__name__)
app.add_middleware(
    ProxyHeadersMiddleware,
    trusted_proxies=("127.0.0.1", "::1"),
)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=("example.com", "*.example.com"),
)
```

reverse proxyがheaderを上書きせず追記だけする構成や、広すぎるproxy信頼範囲は、
接続元の偽装を許すため使用しないでください。

!!! warning "Supervisorを直接公開しない"
    SupervisorをInternetへ直接公開すると、workerからはすべての接続がloopbackに
    見えます。実client IPをキーとする`RateLimitMiddleware`や監査ログには利用できず、
    全利用者が同一clientとして扱われます。client IPが必要な本番構成では、上記の
    reverse proxyと`ProxyHeadersMiddleware`の組み合わせが必須です。

## TLS、host、cookie

TLSはCloudflare、Caddy、nginxなどの信頼済みreverse proxyで終端します。

- `TrustedHostMiddleware`で公開hostを限定する
- CORS originはワイルドカードではなく正確に指定する
- session cookieに`secure=True`、`httponly=True`、適切な`samesite`を設定する
- `SecurityHeadersMiddleware`を利用し、必要に応じてCSPをアプリ固有に調整する
- uploadの容量、拡張子、保存先を制限する

## workerとアプリケーション状態

startup/shutdown hook、インメモリcache、rate-limit counterはworkerごとに独立します。
複数worker間で共有が必要な状態は、外部の永続ストアに置いてください。SQLiteを
複数workerから更新する場合は、write contentionとbackup/recoveryを設計し、負荷に
応じてclient/server型データベースを検討します。

同期WebSocket handlerにはworkerごとの同時実行上限があります。長時間ブロックする
処理はasync handlerまたは外部jobへ移し、HTTPとWebSocketの両方を負荷試験してください。

## graceful shutdown

Unixでは`SIGTERM`でgraceful shutdownが開始されます。orchestratorの停止猶予は、
実行中requestとWebSocketを閉じる時間を含めて設定してください。強制終了に備えて、
handlerは冪等にし、永続化処理を短いtransactionに保ちます。

## Secrets

秘密情報は環境変数またはsecret managerから読み込み、リポジトリへ保存しません。

```text
HIGUMA_SECRET_KEY=...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
```

本番と開発では異なる値を使い、cookie署名keyやOAuth client secretを定期的に
ローテーションできる運用を準備してください。

## チェックリスト

- reverse proxyだけを公開し、Supervisorはprivate/loopbackにbindした
- proxyがforwarded headerを上書きし、workerはloopbackだけを信頼する
- HTTPSを強制し、`debug=False`にした
- 32バイト以上の推測不能なsecret keyを設定した
- host、CORS、cookie、CSRF policyを明示した
- request/upload上限と保存先を設定した
- databaseのbackupとrestoreを試験した
- worker数、connection上限、restart上限を負荷試験した
- shutdown、worker crash、WebSocket切断を試験した
- dependencyとHigumaのsecurity updateを追跡する
