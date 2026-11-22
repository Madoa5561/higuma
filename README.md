<p align="center">
  <a href="https://higuma.moyashi.xyz/">
    <img src="https://raw.githubusercontent.com/Madoa5561/higuma/main/docs/assets/higuma-mark.png" width="160" height="146" alt="higuma — 日本のヒグマをモチーフにしたロゴ">
  </a>
</p>

<h1 align="center">higuma</h1>

<p align="center">
  <strong>Pythonで書き、Rustで配信する。</strong><br>
  A Flask-inspired Python web framework with a Rust HTTP core.
</p>

<p align="center">
  <a href="https://github.com/Madoa5561/higuma/actions/workflows/ci.yml"><img src="https://github.com/Madoa5561/higuma/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <a href="https://github.com/Madoa5561/higuma/actions/workflows/security.yml"><img src="https://github.com/Madoa5561/higuma/actions/workflows/security.yml/badge.svg?branch=main" alt="Dependency security checks"></a>
  <a href="https://pypi.org/project/higuma/"><img src="https://img.shields.io/pypi/v/higuma?color=184c3b" alt="PyPI version"></a>
  <a href="https://pypi.org/project/higuma/"><img src="https://img.shields.io/pypi/pyversions/higuma" alt="Supported Python versions"></a>
  <a href="https://github.com/Madoa5561/higuma/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Madoa5561/higuma" alt="MIT license"></a>
</p>

<p align="center">
  <a href="https://higuma.moyashi.xyz/">日本語ドキュメント</a> ·
  <a href="https://higuma.moyashi.xyz/en/">English documentation</a> ·
  <a href="https://higuma.moyashi.xyz/api/">API reference</a> ·
  <a href="https://github.com/Madoa5561/higuma/tree/main/examples">Examples</a> ·
  <a href="https://github.com/Madoa5561/higuma/releases">Releases</a>
</p>

---

higumaは、書き慣れたPythonのdecorator APIに、axum / Tokioを使ったRustの実行基盤を
組み合わせたWebフレームワークです。JSON API、HTML SSR、リアルタイム通信を、
Python側の必須runtime dependencyなしで構築できます。
名前は日本の**ヒグマ**に由来します。

> **0.11.18** — ルート宣言 `status_code` の `(body, headers)` 2-tuple 適用とデバッグ時
> `traceback.format_exception()` 化、`errorhandler`/`middleware`/`Blueprint` 登録時の厳格検証、
> ASGI／WebSocket での空・重複 `Origin` 拒否・`raw_path=b""` フォールバック・テキスト/close 引数検証、
> `TestClient`/`ASGITestClient` の `base_url` ポート検証・`HEAD` `204`/`205`/`304` `Content-Length`
> 非付与、native `HigumaCore.run(port=0)` 実バインドポート表示を改善。必須 Python 依存は 0 のままです。
>
> **0.11.17** — `_CurrentUserProxy` の `__eq__`/`__hash__` 実装、`AuthManager` の
> `SessionMiddleware` 未設定エラー明確化・`session_key` 検証、認証デコレータの `async def` 保持＆
> claim 検証、`OAuth2Client` の HTTPS/URL/空 `code` 検証、CLI のドット属性パス読み込み
> （`module:Holder.create()`）と入れ子 Blueprint コマンド衝突検出を改善。必須 Python 依存は 0 のままです。
>
> **0.11.16** — `serialize_json_value()` の dataclass 非 deepcopy 化＆`Decimal` 対応、
> `UUID`/`date`/`datetime` の非文字列入力拒否と数値制約での `bool` 拒否、OpenAPI の
> `Literal[Enum]` 値正規化・`responses` 非破壊化・`204`/`304` `content` 省略・
> `PathParam`/`Body` メタデータ反映、`Supervisor(port=0)` 実バインドポート表示を改善。必須 Python 依存は 0 のままです。
>
> **0.11.15** — WSGI マウント（`wsgi_view`）の PEP 3333 `start_response(..., exc_info)` 準拠・
> エンコード済み `/` 越境防止・ポート範囲検証、multipart `max_form_parts` 後の不正終端 `400` 判定、
> 組み込み ORM の `limit`/`offset` 付き `delete()` 拒否・`Model` 型検証・close 済み接続の例外隠蔽防止、
> `JSONLogFormatter` の `default=str`/`exc_info` 対応・`HealthChecks` の async callable probe 対応・
> `StaticFiles` `index_file` 検証を改善。必須 Python 依存は 0 のままです。
>
> **0.11.14** — `TrustedHostMiddleware` の port `0` 拒否・IPv6 正規化（`::1`/`[::1]`）、
> `SecurityHeadersMiddleware` の `None` 無効化修正、`ProxyHeadersMiddleware` の IPv4-mapped IPv6 対応、
> `CSRFProtection` / `TokenSigner` / `PasswordHasher` / `validate_user_id` 入力検証、
> `session |= {...}` 対応、`get_flashed_messages(category_filter=...)` 単一文字列拒否、
> `HTTPException`/`abort`・`ViteManifest(base_url=...)`・SQLAlchemy 拡張の事前検証を改善。必須 Python 依存は 0 のままです。
>
> **0.11.13** — `AwaitBridge` (`_AwaitChannel`) の解除済み `ContextVar` リセット、`Signal` の
> 値型 sender（`str`/`int` 等）一致・async callable 拒否・`connected_to` 同期化、ASGI async fast path の
> finalizer 例外境界分離、`Response`/`RedirectResponse`/`StreamingResponse` 等の重複ペアヘッダ保持、
> `Request.close()` 全資源解放・`WebSocket` payload 検証＆構造化 `send_json()`・`asend()` 追加、
> CLI bool 既定値検証、native `RequestBodyFile.read(None)`／close 後 `ValueError` を改善。必須 Python 依存は 0 のままです。
>
> **0.11.12** — `ASGITestClient(root_path=...)` での `follow_redirects=True`（相対／同一 mount
> リダイレクトの二重 prefix 防止・mount 外リダイレクト拒否・`max_redirects` 検証）、`Higuma`/`Blueprint`
> 登録時の `methods`/`tags`/`allowed_origins` 単一文字列・空 `methods=[]`・`bool` 制限値拒否および
> `MethodView` 継承、`mount_wsgi()`/`mount_asgi()` の原子性、`test_request_context()` の form 制限反映、
> `url_for()` の大文字 `_scheme` 正規化、`TestClient` multipart ヘッダのエスケープを改善。必須 Python 依存は 0 のままです。
>
> **0.11.11** — native サーバーでの不明長 `StreamingResponse` に対する `HEAD` 応答で誤った
> `Content-Length: 0` が付与されないよう修正、native WebSocket の `Close` 後タイムアウト境界と
> preflight 安全ヘッダの `101` 引き継ぎ、`Observability` middleware のエラー応答 `X-Request-ID`
> 付与・小文字 method 正規化・`RedisExtension` URL 検証を改善。必須 Python 依存は 0 のままです。
>
> **0.11.10** — `LocalProxy` (`request` / `current_app` / `current_user`) の `__setattr__`/`__delattr__` 委譲による
> リクエスト間状態リーク防止、`AuthManager.login_user()`/`logout_user()` の `current_user` 即時反映、
> `OAuth2Client` の `scopes` 検証・プリセット上書き・`GET userinfo` ヘッダ修正、`SessionMiddleware`/ORM
> `Boolean`・`Blob`/`Database.connect()`/`Config.from_prefixed_env()` の堅牢化を改善。必須 Python 依存は 0 のままです。
>
> **0.11.9** — 長大 `<int:...>`/`<float:...>` URL セグメントによる 500 クラッシュ・`inf` 防止と
> 重複ルートパラメータ名拒否（Rust/Python 双方）、`IntEnum` クエリ変換・RFC 3339 `z`・`Parameter` 境界検証、
> OpenAPI 同名モデル衝突の自動分離と `str`/`bytes` `Body()` media type 整合、`StaticFiles` 非 ASCII
> `308` リダイレクトと `Accept` OWS 対応を改善。必須 Python 依存は 0 のままです。
>
> **0.11.8** — ASGI 500 フォールバック時のヘッダ／`HEAD` 応答整合、不正 WebSocket scope/`spec_version` 耐性、
> Python/Rust 双方での WebSocket same-origin 空 Host・`:0` 拒否と IPv6 正規化、`Response.set_cookie()` の
> `timedelta` 対応・属性インジェクション防止、`StreamingResponse` 全資源クローズ、`Supervisor` bind 失敗時の
> シグナル復元、`Higuma.run()`／CLI／WSGI マウント境界を改善。必須 Python 依存は 0 のままです。
>
> **0.11.7** — `CORSMiddleware`／`TrustedHostMiddleware`／`ProxyHeadersMiddleware` の単一文字列誤指定防止、
> `TokenSigner`／`RateLimitMiddleware`／`generate_user_id()`・`HealthChecks` の入力検証と終了時同期、
> `Database.session()` の lock リーク修正と `Database.close()` 追加、native `add_route()` の原子性確保、
> `from __future__ import annotations` 下での CLI command 登録を改善。必須 Python 依存は 0 のままです。
>
> **0.11.6** — `mount_wsgi("/")`／`mount_asgi("/")`／`static_url_path="/"` の二重スラッシュ登録修正、
> 空 `200` 応答での `Content-Length: 0` 出力、URL-encoded form の `MAX_FORM_PARTS` 強制、
> 大文字小文字非依存 `Range: bytes=`・`StreamingResponse` header 正規化、ORM `default` 遅延評価・
> `limit(0).first()`・未保存 `delete()` 防止を改善。必須 Python 依存は 0 のままです。
>
> **0.11.5** — `PasswordHasher` の OWASP `scrypt` (`n>=2**15`) 対応と 256 MiB 上限、
> `from __future__ import annotations` 下のローカル dataclass/TypedDict 検証・OpenAPI 解決、
> `StaticFiles` の `root_path` 維持リダイレクト、IPv6 `Supervisor` bind、multipart `7bit`／RFC 2231、
> `ASGITestClient` リダイレクト query 保持、非 ASCII 静的ルート `url_for` エンコードを改善。必須 Python 依存は 0 のままです。
>
> **0.11.4** — nativeサーバーの末尾`/`維持（`mount_static(..., html=True)`の無限リダイレクト解消）、
> IPv6 bind・IPv6 WebSocket same-origin・close検証、RFC 9110重複header統合、
> `Observability` middlewareの4xx/cancel記録、`make_response`重複header組・SSE改行仕様準拠、
> 認証・CSRF・OAuth2・ORM `save()`境界を改善。必須Python依存は0のままです。
>
> **0.11.3** — `mount_asgi`の非同期sub-app切断制御、堅牢な`Request.cookies`解析、
> `MethodView`/`View`の型付き引数・DI・OpenAPI対応、lifespanの同一イベントループ維持、
> `ASGITestClient`の`follow_redirects`対応を改善。必須Python依存は0のままです。
>
> **0.11.2** — ASGI監視・WebSocket切断時やhook差替時の終了処理、`response.background`再代入、
> `StreamingResponse`の`content_length`・`HEAD`一貫性、native WebSocket/205応答の後処理を改善。
> [終了処理と運用上の境界](docs/api.md#background-task)。必須Python依存は0のままです。
>
> **0.11.1** — 非同期background、context内upload、stream完了・HEAD・切断時の後片付けを修正。
> [終了処理と運用上の境界](docs/api.md#background-task)。必須Python依存は0のままです。
>
> **0.11.0** — Flask風URL hooks、Blueprintのアプリ全体hooks、設定loader・階層付き環境変数、
> JSON helper互換性を改善。[使い方と移行時の注意](docs/flask-compat.md)。
>
> **0.11.0に含まれる0.10開発分** — 条件付きASGI高速経路、async DB、Flask風Signals、Redis共有session/quota、
> request ID・metrics・任意tracing・health checks。[導入と対応境界](docs/operations.md)。
>
> **0.11.0に含まれる0.9開発分** — 親子Blueprint、独自CLI、非同期テストクライアント、任意のSQLAlchemy／Redis連携、
> 大容量入力のディスク退避を追加。Flask風のまま分割アプリを育てる開発体験を整えました。
> [開発ガイド](docs/developer-experience.md) / [連携と実用サンプル](docs/integrations.md) /
> [アップロードの境界](docs/uploads.md) / [実測と制約](docs/performance.md) /
> [変更履歴](CHANGELOG.md)

[はじめる](#quick-start) · [型付きAPI](#型付きapi) · [機能](#主な機能) ·
[フロントエンド](#csrspaとフロントエンド) · [対応範囲](#対応範囲) ·
[運用](#本番運用) · [貢献する](#開発とコントリビューション)

## Quick start

CPython 3.10以降でインストールできます。

```bash
python -m pip install -U higuma
```

`app.py`を作成します。

```python
from higuma import Higuma, request

app = Higuma(__name__)


@app.get("/")
def index():
    return {"message": "Hello from higuma"}


@app.post("/echo")
def echo():
    return {"received": request.json}


if __name__ == "__main__":
    app.run()
```

起動後、<http://127.0.0.1:8000> を開きます。

```bash
python app.py
```

CLIからも起動できます。

```bash
higuma run app:app --host 127.0.0.1 --port 8000
higuma routes app:app
higuma run app:app --processes 4 --max-connections 1024
```

GETの応答を確認したら、[Getting started](https://higuma.moyashi.xyz/getting-started/)で
request、template、CLIの使い方へ進めます。

## 型付きAPI

型付きAPIでは、標準の`Annotated`とdataclassだけで入力元、制約、dependency、出力を宣言できます。

```python
from dataclasses import dataclass
from typing import Annotated

from higuma import Body, Depends, Higuma, QueryParam

app = Higuma(__name__)


@dataclass
class Item:
    name: str
    price: float


def page_size(limit: Annotated[int, QueryParam(ge=1, le=100)] = 20):
    return limit


@app.post("/items", response_model=Item, status_code=201)
def create_item(
    item: Annotated[Item, Body()],
    limit: Annotated[int, Depends(page_size)],
):
    return item
```

入力はruntimeで検証・変換され、不正時は構造化された422になります。schemaは同じ宣言から
OpenAPI 3.1へ反映されます。

型付きルートの実行例は[`examples/modern_typed_api.py`](examples/modern_typed_api.py)、
宣言と制約の詳細は[APIリファレンス](https://higuma.moyashi.xyz/api/)を参照してください。

## 主な機能

| 分野 | 機能 |
| --- | --- |
| HTTP | typed input、dependency injection、path converter、自動HEAD/OPTIONS、async handler |
| Response | response model、HTML、JSON、cookie、Range file、sync/async streaming、background task |
| Realtime | SSE、text/binary/JSON WebSocket、Origin検証、認証preflight、bounded queue |
| SSR | MiniJinja、compiled template cache、context processor |
| 構成 | Blueprint、class-based view、lifespan、WSGI/ASGI mount、startup/shutdown hook |
| Flask風API（0.7.0以降） | scoped Blueprint hooks/errors/templates、request context、after_this_request、request teardown |
| API設計 | OpenAPI 3.1、Swagger UI、dataclass/TypedDict/enum/UUID/date schema |
| Data | dependency-free SQLite ORM、transaction、pagination、typed field |
| Auth | signed cookie session、login/role/permission、OAuth 2.0 + PKCE |
| Security | scrypt password、signed token、CSRF、CORS、rate limit、trusted host/proxy |
| Operations | test client、CLI、multi-process supervisor、worker restart limit |
| Server interfaces（0.8.0以降） | `app.asgi_app`、`app.wsgi_app`、同じASGI loopでのasync lifespan / handler |
| Frontend（0.6.0以降） | SPA history fallback、複数static root、Vite manifest、cache / MIME制御 |

完全な一覧と注意点は[機能ガイド](https://higuma.moyashi.xyz/features/)および
[APIリファレンス](https://higuma.moyashi.xyz/api/)を参照してください。

> **Validationについて:** `Annotated[..., Body()]`等のmarkerと`response_model`はruntime検証を
> 有効にします。従来の`request_body=`やreturn annotation単独はschema metadataです。

## CSR/SPAとフロントエンド

以下の機能にはhiguma 0.6.0以降が必要です。0.5.0から利用する場合は更新してください。

```python
from higuma import Higuma

app = Higuma(__name__, static_folder=None)


@app.get("/api/status")
def status():
    return {"status": "ok"}


# 先にフロントエンドをビルドし、index.htmlを含む公開用フォルダを用意します。
app.mount_spa("/app", "frontend/dist")
```

- **CSR** — 深いURLの再読み込みに対応し、APIや見つからないassetをHTMLに置き換えません。
- **静的配信** — `mount_static()`で公開先を分離し、HTMLの再検証とassetのcacheを設定できます。
- **Vite + Python HTML** — `ViteManifest`でhash付きJS・CSSとmodulepreloadを解決します。

React / Vue等のビルド、HMR、Node SSRの実行環境はフロントエンド側で用意します。
設置パス、cookie認証、CORS、デプロイの注意は
[フロントエンドガイド](docs/frontend.md)（[English](docs/en/frontend.md)）を参照してください。

## ASGI／WSGIで実行する

既存ASGI基盤には`asgi_app = app.asgi_app`、WSGI基盤には`wsgi_app = app.wsgi_app`を公開します。
例えばASGIでは、アプリmoduleに`asgi_app`を定義したうえで:

```bash
python -m pip install "higuma[asgi]"
uvicorn app:asgi_app --lifespan on --ws websockets-sansio
```

ASGIでも同期APIとの互換処理に専用workerを使用します。WSGIには標準WebSocket／lifespanがありません。
外部server利用時の性能や接続数、proxy trust、resourceの扱いは
[サーバーインターフェースガイド](https://higuma.moyashi.xyz/server-interfaces/)を確認してください。
Python必須runtime依存0は維持し、ASGI server関連は任意extraです。

## 対応範囲

- Python: CPython 3.10以降（CIでは3.10〜3.14を検証）
- OS: Windows x86-64、Linux x86-64 / ARM64、macOS x86-64 / Apple Silicon
- Linux: manylinux wheelとmusllinux x86-64 wheelを配布
- Database: 組み込みORMはSQLiteのみ
- Protocol: 組み込みHTTP serverとWebSocket。TLSはreverse proxyで終端

wheelがないplatformではsource distributionからRust buildが必要です。公開済みの配布物は
[PyPI](https://pypi.org/project/higuma/)で確認できます。

### 互換性と設計上の境界

higumaはFlaskのdrop-in replacementではありません。組み込みRust serverに加え、
0.8.0から`app.asgi_app`／`app.wsgi_app`を外部サーバーへ渡せます。

既存のWSGI / ASGI applicationはHTTPのroute配下へmountできますが、mount先の
WebSocket、ASGI lifespan、無期限streamingを提供するものではありません。
組み込みのbackground taskとrate limitはprocess-localです。耐久性のあるjobや
分散状態には外部基盤を使用してください。

詳細は[Flask互換性](https://higuma.moyashi.xyz/flask-compat/) /
[English compatibility guide](https://higuma.moyashi.xyz/en/flask-compat/)と
[機能対応表](docs/capabilities.md)にまとめています。

## Examples

[`examples/`](examples)には、最小アプリだけでなく次の実行例があります。

- routing、async handler、型付き入力、dependency injection、class-based view
- response、cookie、file、multipart upload、streaming、SSE、background task
- Blueprint、lifespan、WebSocket、WSGI/ASGI mount、OpenAPI
- SQLite ORM、session authentication、role/permission、OAuth
- security middleware、signed token、in-process testing
- multi-process supervisor

| やりたいこと | 実行例 |
| --- | --- |
| 型付きAPI・入力検証 | [`modern_typed_api.py`](examples/modern_typed_api.py) |
| session・context・flash | [`flask_compat.py`](examples/flask_compat.py) |
| Blueprint hooks・CSRF・session保存 | [`flask_lifecycle.py`](examples/flask_lifecycle.py) |
| 既存Flaskアプリを接続 | [`flask_mount.py`](examples/flask_mount.py) |
| 組み込み・ASGI・WSGIで同じアプリを実行 | [`server_modes.py`](examples/server_modes.py) |
| リアルタイムチャット | [`websocket_chat.py`](examples/websocket_chat.py) |
| streaming・SSE | [`streaming_sse.py`](examples/streaming_sse.py) |
| SQLiteとtransaction | [`orm_blog.py`](examples/orm_blog.py) |
| 認証・CSRF・proxy設定 | [`security_hardened.py`](examples/security_hardened.py) |
| CSR / Vite連携（0.6.0以降） | [`csr_app.py`](examples/csr_app.py) / [`vite_ssr.py`](examples/vite_ssr.py) |

必要な環境変数と実行コマンドは[Examplesガイド](https://higuma.moyashi.xyz/examples/)に
まとめています。

## 本番運用

higumaは活発に開発中です。本番採用前に、対象アプリケーションで負荷試験、障害試験、
security reviewを実施してください。

- `debug=False`のまま運用する
- Caddy、nginx、Cloudflare等でTLSを終端しHTTPSを強制する
- 32バイト以上の推測不能なsecretをsecret managerから読み込む
- `TrustedHostMiddleware`と、正確なCORS / WebSocket Originを設定する
- `ProxyHeadersMiddleware`では直接接続するproxyだけを信頼する
- upload size、保存先、拡張子をアプリケーション側でも制限する
- SQLite fileを定期backupし、multi-process時のwrite負荷を測定する
- network timeout、request buffering、connection limitはreverse proxyでも設定する

詳しくは[デプロイガイド](https://higuma.moyashi.xyz/deployment/)と
[Security Policy](SECURITY.md)を参照してください。

## 開発とコントリビューション

不具合報告、回帰テスト、ドキュメント改善、互換性の検証を歓迎します。
開発環境の準備にはRust toolchainも必要です。

```bash
python -m pip install -e ".[dev,docs]"
python -m pytest
python -m ruff check python tests examples
python -m ruff format --check python tests examples
cargo fmt --all -- --check
cargo clippy --locked --all-targets --all-features -- -D warnings
cargo test --locked --all-features
mkdocs build --strict
```

変更前に[CONTRIBUTING.md](CONTRIBUTING.md)と、coding agent向けの
[SKILL.md](SKILL.md)を確認してください。

CIではPython / OSのmatrix test、Rustのtest / lint、docs build、配布wheelのsmoke testを
実行します。依存関係の監査は[専用workflow](https://github.com/Madoa5561/higuma/actions/workflows/security.yml)で
実施します。バッジは各workflowへのリンクであり、脆弱性が存在しないことの保証ではありません。

不具合は[Issue tracker](https://github.com/Madoa5561/higuma/issues)へ、
脆弱性は公開Issueではなく[Security Policy](SECURITY.md)の窓口へ報告してください。

## Links

- [日本語ドキュメント](https://higuma.moyashi.xyz/)
- [English documentation](https://higuma.moyashi.xyz/en/)
- [PyPI](https://pypi.org/project/higuma/)
- [Examples](examples)
- [Changelog](CHANGELOG.md)
- [Issue tracker](https://github.com/Madoa5561/higuma/issues)
- [Security Policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)
- [ロゴ・ブランド素材](docs/branding.md)

## ライセンス

[MIT License](LICENSE)
