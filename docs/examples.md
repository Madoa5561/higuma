# Examples

`examples/`は、ひとつの概念を小さく実行できるようテーマ別に分かれています。

| File | 内容 | 実行時の注意 |
| --- | --- | --- |
| `app.py` | routing、JSON、SSR template | なし |
| `blueprint_hooks.py` | Blueprint、config、request/lifecycle hook | なし |
| `routing_async.py` | converter、複数method、async handler | なし |
| `modern_typed_api.py` | Annotated入力、DI、response model、OpenAPI | なし |
| `class_based_views.py` | ViewとMethodView | なし |
| `streaming_sse.py` | sync stream、async SSE、background task | SSEは3秒後に終了 |
| `lifespan_background.py` | async lifespan、app.state、background task | なし |
| `responses_cookies_files.py` | response型、cookie、file download | repository内のtemplateを配信 |
| `websocket_chat.py` | JSON WebSocket echo | WebSocket clientが必要 |
| `multipart_upload.py` | 安全なfile uploadと保存 | request時に`uploads/`へ作成 |
| `mounted_apps.py` | WSGI / ASGI mount | HTTP scopeのみ |
| `openapi_api.py` | 従来形式のdataclassとOpenAPI metadata | metadataのみの形式 |
| `orm_blog.py` | SQLite ORM CRUD | 起動時に`blog.sqlite3`を作成 |
| `orm_fields.py` | 全ORM field、query、save | in-memory SQLiteのみ |
| `authentication.py` | login、CSRF、password、session | secret環境変数が必要 |
| `security_tokens_roles.py` | signed token、role、permission | secret環境変数が必要 |
| `oauth_login.py` | Google OAuth + PKCE/state | credentialsと外部通信が必要 |
| `security_hardened.py` | CORS、host、header、proxy、rate limit | proxy trustを環境に合わせる |
| `testing_client.py` | JSON、cookie jar、multipartのtest | serverを起動せずassertを実行 |
| `supervised_app.py` | multi-process Supervisor | workerからpeerはloopbackに見える |

## 基本の実行

repository rootからeditable installした後に実行します。

```bash
python -m pip install -e .
python examples/routing_async.py
python examples/modern_typed_api.py
python examples/streaming_sse.py
python examples/testing_client.py
```

factory形式の例はCLIからも起動できます。

```bash
higuma run examples.orm_blog:create_app --port 8000
higuma run examples.blueprint_hooks:create_app --port 8000
higuma run examples.supervised_app:app --processes 4
```

## secretを使う例

32バイト以上のランダムな値を開発用に設定してください。実際のsecretをshell historyや
repositoryへ保存しないでください。

=== "PowerShell"

    ```powershell
    $env:HIGUMA_SECRET_KEY = python -c "import secrets; print(secrets.token_urlsafe(32))"
    python examples/authentication.py
    ```

=== "POSIX shell"

    ```bash
    export HIGUMA_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
    python examples/authentication.py
    ```

CSRF付きloginでは、まず`GET /csrf`で同じclient sessionにtokenを作り、その値を
`X-CSRF-Token` headerまたは`_csrf_token` form fieldとして`POST /login`へ送ります。

OAuth例では追加で`GOOGLE_CLIENT_ID`と`GOOGLE_CLIENT_SECRET`が必要です。
redirect URIをprovider側にも正確に登録してください。例は実際にGoogleへ通信します。

## fileを作る例

`multipart_upload.py`の既定保存先は`examples/uploads/`です。
`HIGUMA_UPLOAD_DIR`で専用ディレクトリへ変更できます。例はfilenameを正規化し、
2 MiBと許可拡張子を検査しますが、本番では内容検査、malware scan、認可、quota、
object storageも追加してください。

`orm_blog.py`は`examples/blog.sqlite3`を作ります。副作用なくORMを試す場合は
in-memory SQLiteを使う`orm_fields.py`を実行してください。

Supervisorの本番構成は[デプロイ](deployment.md)を参照してください。Supervisorを
直接公開した場合、client IPベースのrate limitや監査ログは利用できません。

[examplesをGitHubで開く](https://github.com/Madoa5561/higuma/tree/main/examples)
