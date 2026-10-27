# 外部ライブラリ連携（0.9+）

基本パッケージの必須Python依存は0のままです。SQLAlchemy/redis-pyを使う場合だけ導入します。

```bash
python -m pip install "higuma[integrations]"
```

Flask-SQLAlchemy / Flask拡張の`init_app`を無修正で使う互換層ではありません。
SQLAlchemy・redis-py自身のAPIを、Higumaのapp context・DI・lifespanへ接続します。

## SQLAlchemy

```python
from typing import Annotated, Any
from sqlalchemy import text
from higuma import Depends, Higuma, SQLAlchemyExtension

app = Higuma(__name__)
database = SQLAlchemyExtension()
database.init_app(app, "sqlite:///application.sqlite")

@app.get("/health/db")
def database_health(db: Annotated[Any, Depends(database.session)]):
    return {"value": db.scalar(text("select 1"))}
```

`database.session`はrequest依存としてsessionを生成し、cleanup時にcloseします。
未commitの変更はrollbackされます。成功応答を送ってからcommitすることはありません。
書き込みは以下のように明示的に確定してください。

```python
with database.transaction() as db:
    db.add(record)
# この時点でcommit成功を確認してからHTTP成功応答を返す。
```

同じextensionを複数appへ初期化してもengine/session factoryはappごとに分離します。
1つのSessionを複数request/threadで共有しません。engineはshutdownでdisposeします。
WSGI利用時はサーバーのworker lifecycleで`app.lifespan()`を管理してください。

このadapterは同期SQLAlchemy 2.x用です。AsyncSession、他DBのdriver、migrationは別途構成します。
SQLiteを使う例の成功をPostgreSQL/MySQLでの本番検証と同一視しないでください。
`init_app`自体はschemaを変更しません。

## Redis

同期clientはnative/WSGI、async clientはASGIでの共有loop用です。

```python
from higuma import Higuma, RedisExtension

app = Higuma(__name__)
cache = RedisExtension()
cache.init_app(app, "redis://127.0.0.1:6379/0", async_client=True,
               decode_responses=True)

@app.get("/count")
async def count():
    return {"count": await cache.client().incr("example:count")}

asgi_app = app.asgi_app
```

native/WSGIでは`async_client=False`と同期handlerを使います。clientはstartupで生成し、
既定でPINGを確認、shutdownでcloseします。`ping_on_startup=False`で遅延接続にもできます。
未起動のclient取得はエラーです。接続失敗時も作成済みclientをcloseします。
既定の接続／操作timeoutは各5秒で、redis-pyのkeyword optionsで上書きできます。
RESP2を明示し、redis-py世代による既定protocolの変更を避けます。

実行可能な例は`python -m examples.redis_counter`です。専用Redisを起動してから実行し、
`POST /counter/bear`でincrementします。デモはlocalhostだけで待ち受けます。

この例のlocalhost・認証なしURLは開発用です。本番はTLS・ACL・secret管理を構成し、
URLをログへ出さないでください。永続job queue、分散rate limit、Redis session storeを
内蔵するという意味ではありません。

## 分割した実用サンプル {#project-example}

repository checkoutから実行します。

```bash
python -m pip install -e ".[integrations,asgi]"
higuma --app examples.project:create_demo_app init-db
higuma --app examples.project:create_demo_app create-user bear --admin
higuma --app examples.project:create_demo_app run --port 8000
```

passwordは対話入力で、引数やログに出しません。`http://127.0.0.1:8000/`からログインし、
管理画面でノートを追加できます。`GET/POST /admin/notes/api`は同じ権限・CSRF境界のJSON APIです。
DBは`target/project-demo/notes.sqlite`へ作成します。init-dbは不足tableの作成でありmigrationではありません。
デモの署名keyはprocessごとに生成するため、再起動でsessionが失効します。実運用では独自factoryから
`create_app(database_url=..., secret_key=...)`へ安定したsecretを渡し、HTTPS用のsecure Cookie、
trusted host/proxy、rate limit、アカウント管理を整えてください。デモをそのまま公開しません。

構成はfactory、`models.py`、認証Blueprint、管理親Blueprint＋notes子Blueprint、template/staticです。
SQLAlchemyで保存、password hashing、login/logout、CSRF、roleチェック、CLIからの初期化を
一連のテストで検証します。password再設定・メール・MFAなどは実装していません。

## 検証

```bash
python -m pytest tests/test_integrations.py tests/test_project_example.py
```

Redis実通信テストには、専用の一時Redisを用意し`HIGUMA_TEST_REDIS_URL`を渡します。
本番URLは使いません。テストはランダムprefixの自分のkeyだけを削除し、FLUSHDB/FLUSHALLを実行しません。
接続先未指定時のskipは実通信成功を意味しません。CIには専用Redis serviceを用意します。

上流仕様: [SQLAlchemy Session](https://docs.sqlalchemy.org/en/20/orm/session_basics.html)、
[redis-py asyncio](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html)。
