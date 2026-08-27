# higuma

Rust HTTPコアと、書き慣れたPython APIを組み合わせたWebフレームワークです。

[日本語ドキュメント](https://higuma.moyashi.xyz/) / [English documentation](https://higuma.moyashi.xyz/en/)

```bash
python -m pip install -U higuma
```

## どんなフレームワークか

higumaは「アプリケーションはPythonで素直に書き、HTTPの実行基盤はRustに任せる」
ことを目的にしています。

- routing、middleware、hooks、BlueprintはFlaskに着想を得たPython API
- socket、route matching、file streaming、gzip、WebSocketはaxum / TokioベースのRustコア
- MiniJinjaによるSSRと、辞書やlistをそのまま返せるJSON API
- Python側の必須runtime dependencyはゼロ
- CPython 3.10以降で利用できるABI3 wheel

higumaはFlaskのdrop-in replacementではありません。また、higuma自体をWSGI/ASGI
serverへ渡すのではなく、組み込みのRust serverで実行します。既存のWSGI/ASGIアプリは
higumaのroute配下へmountできます。

## Quick start

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

## 主な機能

| 分野 | 機能 |
| --- | --- |
| HTTP | typed input、dependency injection、path converter、自動HEAD/OPTIONS、async handler |
| Response | response model、HTML、JSON、cookie、Range file、sync/async streaming、background task |
| Realtime | SSE、text/binary/JSON WebSocket、Origin検証、認証preflight、bounded queue |
| SSR | MiniJinja、compiled template cache、context processor |
| 構成 | Blueprint、class-based view、lifespan、WSGI/ASGI mount、startup/shutdown hook |
| API設計 | OpenAPI 3.1、Swagger UI、dataclass/TypedDict/enum/UUID/date schema |
| Data | dependency-free SQLite ORM、transaction、pagination、typed field |
| Auth | signed cookie session、login/role/permission、OAuth 2.0 + PKCE |
| Security | scrypt password、signed token、CSRF、CORS、rate limit、trusted host/proxy |
| Operations | test client、CLI、multi-process supervisor、worker restart limit |

完全な一覧と注意点は[機能ガイド](https://higuma.moyashi.xyz/features/)および
[APIリファレンス](https://higuma.moyashi.xyz/api/)を参照してください。

> **Validationについて:** `Annotated[..., Body()]`等のmarkerと`response_model`はruntime検証を
> 有効にします。従来の`request_body=`やreturn annotation単独はschema metadataです。

## 対応範囲

- Python: CPython 3.10以降（CIでは3.10〜3.14を検証）
- OS: Windows x86-64、Linux x86-64 / ARM64、macOS x86-64 / Apple Silicon
- Linux: manylinux wheelとmusllinux x86-64 wheelを配布
- Database: 組み込みORMはSQLiteのみ
- Protocol: 組み込みHTTP serverとWebSocket。TLSはreverse proxyで終端

wheelがないplatformではsource distributionからRust buildが必要です。公開済みの配布物は
[PyPI](https://pypi.org/project/higuma/)で確認できます。

## Examples

[`examples/`](examples)には、最小アプリだけでなく次の実行例があります。

- routing、async handler、型付き入力、dependency injection、class-based view
- response、cookie、file、multipart upload、streaming、SSE、background task
- Blueprint、lifespan、WebSocket、WSGI/ASGI mount、OpenAPI
- SQLite ORM、session authentication、role/permission、OAuth
- security middleware、signed token、in-process testing
- multi-process supervisor

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

## 開発

```bash
python -m pip install -e ".[dev,docs]"
python -m pytest
python -m ruff check python tests examples
cargo test --locked --all-features
mkdocs build --strict
```

変更前に[CONTRIBUTING.md](CONTRIBUTING.md)と、coding agent向けの
[SKILL.md](SKILL.md)を確認してください。

## Links

- [日本語ドキュメント](https://higuma.moyashi.xyz/)
- [English documentation](https://higuma.moyashi.xyz/en/)
- [PyPI](https://pypi.org/project/higuma/)
- [Examples](examples)
- [Changelog](CHANGELOG.md)
- [Issue tracker](https://github.com/Madoa5561/higuma/issues)
- [Security Policy](SECURITY.md)

## ライセンス

[MIT License](LICENSE)
