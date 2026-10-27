# Examples

0.11候補の`python -m examples.flask_migration`は、言語prefixのURL前処理・既定値、
Blueprintの全体hooks、namespace設定・JSON cache指定を依存追加なしで試せます。
[使い方と移行境界](flask-compat.md)を参照してください。

0.10+では`python -m examples.async_ecosystem`（使い捨てSQLite・async API・health・request ID）、
`python -m examples.redis_sessions`（専用Redis・CSRF付き共有session）、
`python -m examples.signals`（app別ライフサイクル通知）を追加しています。
[導入と運用境界](operations.md)を読んでから実行してください。

0.9+の分割アプリは[`examples/project`](https://github.com/Madoa5561/higuma/tree/main/examples/project)です。
factory・親子Blueprint・SQLAlchemy・login/CSRF/role・管理画面・独自CLIを組み合わせます。
[セットアップと注意](integrations.md#project-example)を参照してください。
大容量入力は`examples/server_modes.py`の`POST /upload`、比較測定は[性能ガイド](performance.md)へ。

`examples/`は、ひとつの概念を小さく実行できるようテーマ別に分かれています。

| File | 内容 | 実行時の注意 |
| --- | --- | --- |
| `app.py` | routing、JSON、SSR template | なし |
| `csr_app.py` | CSR、History API、API JSON、深いURLのreload | 0.6.0以降、`/app/`を開く。npm不要 |
| `vite_ssr.py` | Python HTML＋Vite CSS/modulepreload | 自分のbuildと`.vite/manifest.json`が必要 |
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
| `server_modes.py` | 同じアプリを組み込み／ASGI／WSGIで実行、async WS・SSE | ASGIは任意extra、WSGIはHTTPのみ |
| `flask_mount.py` | 実際のFlaskアプリをWSGI接続、日本語URL | `pip install "higuma[compat]"` |
| `flask_compat.py` | g、session、flash、url_for、CSRF | secret環境変数が必要 |
| `flask_lifecycle.py` | Blueprint限定hooks、teardown、after callbackからのsession保存 | 0.7.0以降、secret環境変数が必要 |
| `openapi_api.py` | 従来形式のdataclassとOpenAPI metadata | metadataのみの形式 |
| `orm_blog.py` | SQLite ORM CRUD | 起動時に`blog.sqlite3`を作成 |
| `orm_fields.py` | 全ORM field、query、save | in-memory SQLiteのみ |
| `authentication.py` | login、CSRF、password、session | secret環境変数が必要 |
| `security_tokens_roles.py` | password login、CSRF、signed token、role、permission | secretとpassword hash環境変数が必要 |
| `oauth_login.py` | Google OAuth + PKCE/state | credentialsと外部通信が必要 |
| `security_hardened.py` | CORS、host、header、proxy、rate limit | proxy trustを環境に合わせる |
| `testing_client.py` | JSON、cookie jar、multipartのtest | serverを起動せずassertを実行 |
| `supervised_app.py` | multi-process Supervisor | workerからpeerはloopbackに見える |

## 基本の実行

外部サーバー対応の例は[ASGI・WSGIガイド](server-interfaces.md)を参照してください。
`uvicorn examples.server_modes:asgi_app --lifespan on --ws websockets-sansio`、
`python -m examples.server_modes --mode wsgi`、`python -m examples.server_modes`で
同じHTTP endpointを3つの実行基盤で試せます。reference WSGI serverは開発用です。

CSRは`higuma run examples.csr_app:create_app`で起動し、`http://127.0.0.1:8000/app/`を開きます。
`vite_ssr.py`は`HIGUMA_FRONTEND_DIST`に既存のbuild directoryを設定してから起動します。
manifestはViteの`build.manifest: true`で生成し、baseは`/build/`に合わせます。
詳細な開発／本番手順は[フロントエンドガイド](frontend.md)を参照してください。

追加APIと制限は[Flask互換性](flask-compat.md)を参照してください。
`security_tokens_roles.py`では`HIGUMA_SECRET_KEY`に加えて、
`PasswordHasher().hash(...)`で作った値を`HIGUMA_DEMO_PASSWORD_HASH`に設定します。
GET `/csrf-token`のCookieとtokenを保持し、POSTへ`X-CSRF-Token` headerと
`{"user_id": "usr_example123", "password": "your-password"}`を送ります。

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

## request lifecycleを試す {#request-lifecycle}

上の手順で開発用secretを設定し、repository rootから起動します。

```bash
higuma run examples.flask_lifecycle:create_app --port 8000
```

同じCookieを保持するclientで`GET /counter/`を呼びます。responseには`count`と`csrf_token`、
`Server-Timing` headerが含まれます。返されたtokenを`X-CSRF-Token`へ設定し、
`POST /counter/increment`へ`{"increment": 2}`をJSONで送ります。
`X-Counter-Saved: yes`と新しいcountを確認し、次のGETでも値が残ることを確かめます。
tokenなしのPOSTは403、不正なincrementは400です。

serverなしで同じ流れを試す場合:

```python
from examples.flask_lifecycle import create_app

with create_app().test_client() as client:
    token = client.get("/counter/").get_json()["csrf_token"]
    response = client.post(
        "/counter/increment", json={"increment": 2}, headers={"X-CSRF-Token": token}
    )
    assert response.get_json()["count"] == 2
    assert response.headers["X-Counter-Saved"] == "yes"
    assert client.get("/counter/").get_json()["count"] == 2
```

このcountは署名Cookie内のデモ用状態です。永続DBや同時更新に強いcounterではありません。
本番ではHTTPS、`secure=True`、適切なhost/proxy設定と外部state管理を追加してください。
hook順・早期応答・contextの制限は[Flask互換性](flask-compat.md)と[テスト](testing.md)を参照してください。

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
