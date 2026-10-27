# 非同期処理・共有状態・運用監視（0.10+）

基本パッケージの必須Python依存は0です。外部連携は必要なextraだけ導入します。
この章の0.10開発分は0.11.0に含まれます。次のコマンドで導入できます。
`examples/`の実行はrepositoryを取得し、そのroot directoryで行ってください。

```bash
python -m pip install "higuma[asgi,async-db,integrations,observability]==0.11.0"
```

## ASGIの非同期高速経路

`async def`のHTTP routeは、安全条件を満たすとserver loop上で実行し、通常応答の送信で
同期workerとの往復を省きます。`app.config["ASGI_ASYNC_FAST_PATH"] = False`で従来経路へ戻せます。

適用条件は、同期middleware・app/Blueprint hooks・独自error handlers・app teardown・
当該appへのSignals購読がなく、引数に`Depends`/`BackgroundTasks`/`Form`/`File`がなく、入力がdisk spoolされていないことです。
条件を満たさないときは既存の互換経路へ自動的に戻し、hookや認証を飛ばしません。
型付きpath/query/body、request/g、response model、request-local after callbackは維持します。
file/template/stream/backgroundは互換serializerへ渡し、async処理には元のserver loopを使います。
`ASGI_MAX_CONCURRENCY`は高速経路にも適用します。WebSocket・WSGI・nativeの入口は変えません。

高速経路でも入力は受信後dispatchします。`async def`内の同期DB/CPU処理を自動的に並列化しません。
blocking I/Oはasync driverか明示的なthread offloadを使ってください。
「すべてのASGI workloadが速くなった」「Starletteより常に速い」という意味ではありません。
[性能測定](performance.md)と生データを参照してください。

## Async SQLAlchemy

```python
from typing import Annotated, Any
from sqlalchemy import text
from higuma import AsyncSQLAlchemyExtension, Depends, Higuma

app = Higuma(__name__)
database = AsyncSQLAlchemyExtension()
database.init_app(app, "sqlite+aiosqlite://")

@app.get("/db")
async def probe(db: Annotated[Any, Depends(database.session)]):
    return {"value": await db.scalar(text("select 1"))}

asgi_app = app.asgi_app
```

`database.session`はasync generator型DIで、close時に未commitの変更をrollbackします。
書き込みには`async with database.transaction() as db:`を使い、ブロックを抜けてcommit成功後に応答します。
例外・キャンセルもrollback対象です。sessionを複数taskで共有しません。
engine/session factoryはappごと・startupで作成し、所有するASGI loopでshutdown/disposeします。
lifespanを有効にし、startup hookで`database.engine`へ触れるときは`with app.app_context():`を使います。
`init_app`はschemaを変更しません。migration・本番DBdriver・pool設計は別途必要です。
native/WSGIのloop間共有には使いません。実通信検証のDBはSQLiteで、他DBの保証ではありません。

実行例は`python -m examples.async_ecosystem`です。in-memory SQLiteの使い捨てデモとして、
`GET/POST /notes`、health、request ID付き応答を提供します。POSTは`{"title":"bear"}`です。
これは認証なしのlocalhostデモで、公開アプリには認証・権限・入力上限等を追加してください。

## Flask風Signals

```python
from higuma import request_finished, template_rendered

@request_finished.connect_via(app)
def completed(sender, response):
    sender.logger.debug("status=%s", response.status_code)

def capture(sender, template, context):
    assert template == "page.html"

with template_rendered.connected_to(capture, app):
    app.test_client().get("/")
```

`Signal`、`ANY`、`connect`/`disconnect`/`connect_via`/`connected_to`/`send`/
`receivers_for`/`has_receivers_for`を提供します。弱参照が既定で、bound methodとsenderも解放できます。
`connect_via`と一時context購読はcallbackを強参照します。senderを指定してapp間の混線を防いでください。
callbackは同期関数で、例外は呼出元へ伝わります。async callbackとBlinker内部APIは非対応です。

公開通知は`request_started`、`request_finished(response=...)`、
`request_tearing_down(exc=...)`、`got_request_exception(exception=...)`、
`appcontext_pushed`/`appcontext_tearing_down(exc=...)`/`appcontext_popped`、
`before_render_template`/`template_rendered(template=名前のstr, context=dict)`、
`message_flashed(message=..., category=...)`です。
template通知の購読がある場合はdispatch中に実際にrenderし、成功時だけ`template_rendered`を送ります。
通知順はテストでFlaskと比較しますが、Flask/Blinkerのsignal object自体を共有せず、全拡張互換ではありません。
`request_started`はHiguma middlewareの実行前なので、session/userがまだ読み込まれていないことがあります。
`examples/signals.py`も参照してください。contextやCookieをログへ丸ごと出しません。

## Redis sessionと分散rate limit

```python
from higuma import RedisExtension, RedisSessionStore, RedisSessionMiddleware, RedisRateLimitMiddleware

cache = RedisExtension()
cache.init_app(app, "redis://127.0.0.1:6379/0", async_client=False)
store = RedisSessionStore(cache.client, prefix="my-service:", ttl=3600)
app.add_middleware(RedisSessionMiddleware, secret_key, store=store, secure=True)
app.add_middleware(RedisRateLimitMiddleware, cache.client, prefix="my-service:", limit=100, window=60)
```

`secret_key`は32bytes以上の十分なエントロピーを持つsecretを安全に読み込みます。
既存`AuthManager`では`session_middleware=RedisSessionMiddleware`と
`session_options={"store": store, "secure": True}`で差し替えます。二重にsession middlewareを登録しません。

- Cookieには署名付きのランダムIDだけを置き、session内容はRedisへ保存します。暗号化の代わりではありません。
- 保存値はJSONで表現できるデータにします。任意Python objectのpickle保存は行いません。
- アプリごとに固有のprefixが必須です。TTLは既定14日、payload上限64KiB。永続Cookieのmax_ageはTTLに揃えます。
- `session.clear()`後の再ログインでIDを更新し、logoutでは旧IDを削除します。
  管理側は保存した`session.sid`を`store.revoke(sid)`へ渡して失効できます。
  ユーザー単位の全session索引や管理画面はアプリ側で構成します。
- 読取後に失効・更新されたrecordへの再保存はRedis内CASで拒否し、409を返します。
  request中に読み込んだ認証状態まで遡って中断する仕組みではありません。
- TTLは書込時に更新し、読取だけでは延長しません。Redis eviction/期限切れは再ログイン扱いです。
- rate limitはRedis内の原子的な固定window方式です。process-localの既存sliding windowと同じアルゴリズムではありません。
  keyはidentityのSHA-256を使いますが匿名化の保証ではありません。TTL・Redis容量・外側の接続制限が必要です。
- Redis障害は503でfail closed。自動的に署名Cookieやprocess-local quotaへ切り替えません。
  TLS/ACL、timeouts、専用領域、secret管理、共有proxy設定は運用側で用意します。

同期Redis client専用です。ASGIでも互換worker上で動作します。CSRFは別途必要です。
`python -m examples.redis_sessions`は専用のlocalhost Redis向けデモです。
`GET /csrf`でCookieとtokenを取得し、`X-CSRF-Token`を付けて`POST /visits`・`POST /logout`を実行します。

## ログ・metrics・tracing

```python
from higuma import Observability

observer = Observability()  # 外部送信、tracing、ログ出力は既定OFF
asgi_app = observer.wrap_asgi(app)
print(observer.snapshot())  # 保護された管理経路から利用する
```

ASGI wrapperはstream完了・切断までの時間を測り、高速経路を保ちます。native/WSGIでは
`app.add_middleware(observer)`を使えますが、middleware呼出部分の時間であり、lazy stream完了時間ではありません。
両方を同時登録すると二重計測になるので避けます。

request IDは既定でサーバー生成し、応答の`X-Request-ID`へ載せます。ASGIではscope.state、nativeではrequest.stateにも格納します。
`trust_request_id=True`のときだけ、ASCII英数字・`_`・`-`で64文字以内の値を受け入れます。
metricsはroute pattern・既知method・status等で集計し、`max_series`（既定1024）を超える系列はoverflowへまとめます。
process-localのcount・時間合計/最大・in-flightで、複数processの自動集約やhistogramではありません。
metrics endpointは自動公開しません。

`logger=...`を渡すと構造化recordを出し、`JSONLogFormatter`でJSON化できます。
observerはquery・実path値・body・Cookie・Authorization・例外文を記録しません。
アプリ自身のloggerやexporterが出す情報まで自動redactするものではありません。
`tracing=True`は任意のOpenTelemetry APIを使いますが、provider/exporter・送信先・認証はアプリ側で明示設定します。
自動の外部接続・認証設定の読取は行いません。slow exporter/loggerにはbatch処理等を構成してください。

## Liveness / readiness

```python
from higuma import HealthChecks

health = HealthChecks(timeout=1.0)
health.add_check("cache", cache_probe)  # 引数なし、成功時にTrueを返すsync/async関数
health.install(app)
```

`GET /health/live`は依存障害と切り離し、`GET /health/ready`は全probe成功で200、失敗/timeoutで503です。
応答は汎用statusだけで接続先や例外を出さず、no-storeを付けます。`health.ready=False`でdrain状態へ変更できます。
timeout中の同じprobeを無制限に再投入せず、同期probeのthread数も制限します。
Pythonは停止しない同期関数を強制終了できません。probe自身のI/O timeoutも必須で、
async処理もキャンセルに協調する必要があります。
readinessはDB/schema migrationや実際のユーザーフローのE2E保証ではありません。

## 検証・依存監査

`tests/test_async_fast_path.py`、`test_async_database.py`、`test_signals.py`、
`test_redis_state.py`、`test_observability.py`に機能別テストがあります。
Redis実通信には専用の`HIGUMA_TEST_REDIS_URL`を渡します。自分のUUID prefixのkeyだけを片付けます。
Python 3.10の実行時依存とPython 3.14の開発/監査ツールを別環境で監査し、既知脆弱性をignoreして通しません。
古いPython環境への監査ツール一括導入は、上流ツールのPythonサポート範囲に注意してください。

上流資料: [SQLAlchemy asyncio](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)、
[Flask Signals](https://flask.palletsprojects.com/en/stable/signals/)、
[Redis EVAL](https://redis.io/docs/latest/commands/eval/)、
[OpenTelemetry Python](https://opentelemetry.io/docs/languages/python/instrumentation/)。
