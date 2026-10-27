# Flask風のままアプリを育てる（0.9+）

ルートの書き方だけでなく、部品の分割・管理コマンド・テストを一貫して扱います。
Flask/Click拡張を無修正で動かすという意味ではありません。

## ネストしたBlueprint

```python
from higuma import Blueprint, Higuma, request, url_for

app = Higuma(__name__)
admin = Blueprint("admin", __name__, url_prefix="/admin")
users = Blueprint("users", __name__, url_prefix="/users")

@users.get("/<int:id>")
def detail(id):
    return {"id": id, "blueprint": request.blueprint,
            "url": url_for(".detail", id=id)}

admin.register_blueprint(users)
app.register_blueprint(admin)
```

URLは`/admin/users/1`、endpointは`admin.users.detail`、`request.blueprint`は
`admin.users`です。相対endpointは最も内側のBlueprintから解決します。

- before: app → 親 → 子。それぞれ登録順。
- after / teardown: 子 → 親 → app。それぞれ逆登録順。
- context processor: app → 親 → 子 → 明示的なtemplate値の順に上書き。
- error handler: 同じHTTP statusなら子 → 親 → app。次に例外型を探索します。
- URL不明の404 / routingの405ではBlueprintが未選択なのでappのhandlerを使います。
- 名前・routeの衝突、循環登録は登録前に拒否します。

`admin.register_blueprint(users, name="members", url_prefix="/members")`で登録名／prefixを
上書きできます。appへの再登録も`app.register_blueprint(admin, name="other", url_prefix="/other")`
のように別名で行います。同名の再登録は拒否します。subdomainやFlask signalsは追加していません。

## 部品ごとのtemplateとstatic

```python
pages = Blueprint("pages", __name__, url_prefix="/pages",
                  template_folder="templates", static_folder="static")
app.register_blueprint(pages)
```

相対ディレクトリは`import_name`のmodule位置を基準にします。明示する場合は
`root_path=...`を渡せます。staticの既定URLは`/pages/static/<path:filename>`、endpointは
`pages.static`です。既存StaticFilesと同じ隠しファイル・path traversal境界を使います。
Blueprintのbefore hooksはstaticにも適用されます。ログイン画面用CSSなどは意図的に公開するか、
認証が不要な別Blueprintへ分けてください。

template探索順はappのtemplate directory → Blueprintの登録順です。これはrequestごとの
自動的な名前空間分離ではありません。`templates/admin/notes.html`のように部品名を
filenameへ含めて衝突を避けます。MiniJinjaとJinja2の完全互換は保証しません。

## アプリ独自のCLI

```python
from higuma import current_app

@app.cli.command()
def greet(name: str, *, count: int = 1, loud: bool = False):
    message = name.upper() if loud else name
    return message * count
```

```bash
higuma --app app:app greet bear --count 2 --loud
higuma --app app:create_app routes
higuma --app app:create_app shell
```

既存の`higuma run app:app` / `higuma routes app:app`も維持します。factoryは引数なしで、
任意のPython式を評価しません。コマンドにはapp contextが有効で、終了時にteardownします。
lifespanは自動開始しません。Redisなどの起動が必要な同期コマンドでは明示的に
`with current_app.lifespan():`を使い、同期clientを選びます。

- `str` / `int` / `float`の必須通常引数は位置引数です。
- default付き・keyword-only引数は`--parameter-name`です。
- default付き`bool`はflagです。Trueがdefaultなら指定時にFalseへ切り替わります。
- `async def`も利用できますが、ASGI serverのlifespanとは別の実行です。
- 戻り値が`None`以外なら表示します。秘密値を戻したり、passwordをCLI引数にしないでください。
- Clickのcommand/context/decoratorや任意の複雑な型annotationとは互換ではありません。

`@users.cli.command()`で登録したコマンドは、上のネストなら
`higuma --app app:app admin users command-name`で実行します。
組み込みの`run` / `routes` / `shell`は予約名です。

## CLIとASGIのテスト {#cli-and-asgi-testing}

```python
result = app.test_cli_runner().invoke(["greet", "bear", "--count", "2"])
assert result.exit_code == 0
assert result.output.strip() == "bearbear"
```

CLI runnerは標準出力を一時的に捕捉するため、同じprocessで並行実行しません。
失敗は`exit_code`と`exception`で確認します。

```python
async def test_http():
    async with app.test_async_client() as client:
        response = await client.get("/admin/users/1")
        assert response.status_code == 200

async def test_socket():
    async with app.test_async_client() as client:
        async with client.websocket_connect("/ws") as socket:
            await socket.send_json({"message": "hello"})
            assert await socket.receive_json() == {"echo": {"message": "hello"}}
```

`ASGITestClient(app, base_url="https://localhost", root_path="/service", timeout=5)`も公開します。
async contextがlifespanとstateを管理します。`get/post/put/patch/delete/options/head/open`は
応答をbufferして通常のResponseを返します。Cookie jarは簡易的なテスト用で、ブラウザの
domain/path/SameSite/expiry規則を完全再現しません。自動redirect追跡は追加していません。

SSEや大きな応答は`async with client.stream(path) as response:`と
`async for chunk in response.aiter_bytes():`を使います。途中終了時にはdisconnectを通知し、
cleanupを待ちます。WSの`receive*`はcloseを`WebSocketDisconnect`として返します。
接続前のOrigin拒否もテストできます。

これはasyncio上のprotocol testで、TLS、proxy、OS socket、server実装そのものを検証するものではありません。
同期の無期限処理を強制終了することもできません。実通信テストと各I/Oのtimeoutを併用します。

## 分割アプリの実例

[`examples/project`](https://github.com/Madoa5561/higuma/tree/main/examples/project)に、factory、
SQLAlchemy、login、CSRF、管理者権限、親子Blueprint、HTML画面とJSON APIをまとめています。
導入手順は[外部連携](integrations.md)、入力の扱いは[アップロード](uploads.md)、
実測条件と結果は[性能](performance.md)を参照してください。
