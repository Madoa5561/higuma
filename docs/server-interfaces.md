# ASGI・WSGIと組み込みサーバー

0.8.0以降は、同じHigumaアプリを3つの入口で実行できます。
`mount_asgi` / `mount_wsgi`（他のアプリを取り込む機能）とは逆方向の対応です。

| 項目 | 組み込みRust server | `app.asgi_app` | `app.wsgi_app` |
| --- | --- | --- | --- |
| HTTP・routing・session・認証・template | 対応 | 対応 | 対応 |
| HTTP response streaming・SSE・file | 対応 | 対応 | 対応。送信中はworkerを使用 |
| WebSocket | 対応 | 対応 | 標準WSGIでは非対応 |
| startup/shutdown・lifespan | `app.run()`が管理 | ASGI lifespan protocol | 標準protocolなし。worker側で明示管理 |
| loopに紐づくasync resourceの共有 | 制約あり | async lifespanとhandlerは同じloop | 一般には保証しない |
| socket・TLS・圧縮・worker数 | Higuma／reverse proxy | 外部ASGI server／proxy | 外部WSGI server／proxy |

Python必須runtime依存は引き続き0です。ただし、どのモードでもHigumaのRust拡張は必要です。
外部モードでもRust template engineは再利用しますが、HTTP通信は外部サーバーが担当します。

## 同じアプリから公開する

```python
from higuma import Higuma

app = Higuma(__name__)

@app.get("/")
def index():
    return {"framework": "higuma"}

asgi_app = app.asgi_app
wsgi_app = app.wsgi_app
```

両propertyは再取得しても同じcallableです。`app`自体はASGI/WSGIの曖昧な呼び分けをせず、
外部サーバーには明示的な入口を渡します。組み込みモードでは従来どおり`app.run()`を使います。

## ASGIで実行する

```bash
python -m pip install "higuma[asgi]"
uvicorn examples.server_modes:asgi_app --host 127.0.0.1 --port 8000 --lifespan on --ws websockets-sansio
```

`examples.server_modes`はrepository内の実行例です。自分のアプリでは`app:asgi_app`等に置き換えます。
任意の`asgi` extraはUvicornとwebsocketsを追加するだけで、本体のimportには不要です。
Python 3.10では対応するwebsockets 16系、3.11以降では新しい対応版が解決されます。

ASGI 3のasyncio対応callableで、HTTP・WebSocket・lifespanを処理します。
trio backend、ASGI 2の旧式callable、全ASGI拡張の対応を約束するものではありません。
今回の実通信検証はUvicornです。他サーバー固有の拡張や設定は利用先で確認してください。

### 非同期処理とlifespan

```python
import asyncio
from contextlib import asynccontextmanager
from higuma import Higuma

@asynccontextmanager
async def lifespan(app):
    # loopに紐づくclient/poolも、この中で作成・終了します。
    yield {"gate": asyncio.Semaphore(10)}

app = Higuma(__name__, lifespan=lifespan)

@app.get("/")
async def index():
    async with app.state["gate"]:
        return {"ready": True}

asgi_app = app.asgi_app
```

async lifespan、async handler、async hook、async dependency、async stream、async background taskは
ASGIサーバーのイベントループで実行します。lifespanがyieldしたmappingは`app.state`へ反映し、
サーバーがlifespan stateに対応する場合は`request.scope["state"]`にも渡されます。
startup/shutdownの失敗はprotocolのfailed eventで通知し、詳細はserver logにだけ記録します。

1つのHiguma instanceは1つの稼働中ASGI lifespan／event loopに割り当ててください。
複数processでは各workerが自分のinstanceとresourceを作ります。loopをまたいでclientを共有しません。
lifespanを無効にするとstartup/shutdownは実行されず、resourceの初期化・終了も自動では行いません。

### 実行モデルと並行数の境界

既存の同期middleware・Flask風hooksとの互換性を維持するため、dispatchの調整処理は専用workerへ
分離し、awaitableをASGI loopへ戻します。サーバーの標準executorとは分離しています。
**async handlerでも接続中の調整workerを使うため、完全にcoroutineだけで動く実装ではありません。**
長時間のWebSocket・SSEも並行数を消費します。大量の接続が必要な用途は実負荷で検証してください。

起動前に設定できます:

| Config | 既定値 | 範囲 |
| --- | --- | --- |
| `ASGI_MAX_CONCURRENCY` | `64` | HTTP／WS共通の同時処理枠と専用worker上限。正の整数 |
| `ASGI_WEBSOCKET_QUEUE_SIZE` | `4` | adapter内の受信message queue。正の整数 |
| `ASGI_WEBSOCKET_MAX_MESSAGE_SIZE` | `1048576` | WS messageのUTF-8/bytesサイズ。超過時1009でclose |
| `MAX_CONTENT_LENGTH` | constructor値（既定8 MiB） | bufferするHTTP request bodyの上限 |

外部サーバー側の接続数・message size・queue・timeoutにも上限を設定します。
サーバー側で既に受信したmessageやbodyのメモリを、このadapterから遡って制限することはできません。
任意の同期Python処理を強制停止することもできません。切断時は実行中のasync処理へcancelを伝え、
workerが終了するまで枠を保持してcleanupします。同期処理自体にもtimeoutを設けてください。

### WebSocket

同期handlerは従来の`send_*` / `receive_*`を使えます。async handlerでは次のAPIを使います:

```python
@app.websocket("/ws", allowed_origins=("https://example.com",))
async def socket(ws):
    value = await ws.areceive_json()
    await ws.asend_json({"echo": value})
    await ws.aclose()
```

`asend_text/asend_bytes/asend_json`、`areceive/areceive_text/areceive_bytes/areceive_json`、
`aclose(code=1000, reason="")`は組み込みモードでも使用できます。
ASGI loop上から同期WS methodを呼ぶと、停止やdeadlockの代わりにエラーにします。

Originと認証middlewareはaccept前に検査します。拒否HTTP responseはサーバーの
`websocket.http.response`拡張がある場合だけ送り、それ以外はupgrade前close（通常403）です。
HTTP用のbefore/after-request hooksはWebSocketの接続前処理では実行しません。
handshakeでCookieを設定する場合はWebSocket ASGI spec 2.1以降が必要です。旧serverには拒否で応答します。
close reasonはspec 2.3以降で送信します。subprotocol選択・任意のWS拡張APIは提供しません。
WS処理中のsession書換えをCookieへ保存することはできません。session更新はHTTP endpointで行います。

## WSGIで実行する

外部サーバーへ`wsgi_app(environ, start_response)`を渡します。
repositoryのローカル実行例はPython標準のreference serverを使います:

```bash
python -m examples.server_modes --mode wsgi --port 8000
```

reference serverは本番用の推奨構成ではありません。実通信確認はwsgiref、protocol確認は
`wsgiref.validate`で実施しています。本番WSGIサーバーは別途用意し、そのworker設定を検証します。

WSGIには標準のstartup/shutdown通知がないため、このcallableだけではlifespanを開始しません。
例のようにworkerの起動・停止全体を`with app.lifespan():`で囲むなど、利用先で管理します。
WSGIでは同期resourceを基本とし、loopに紐づくasync clientの共有を前提にしないでください。

response iterableはbytesを返し、`close()`でstream・file・dependencyの後始末を行います。
サーバー側が`close()`を正しく呼ぶことが必要です。SSEも動きますが長時間workerを占有します。
標準WSGIからWebSocketへupgradeすることはできません。

入力bodyは`CONTENT_LENGTH`の範囲で読みます。長さがない場合は、サーバーが
`wsgi.input_terminated=True`を提供するときだけEOFまで上限付きで読みます。
そうでなければ未指定bodyを空として扱い、socketを無期限に読みません。

## URL・header・middleware

ASGIの`root_path`、WSGIの`SCRIPT_NAME`は`request.root_path`と`url_for`へ反映します。
Swagger UI・OpenAPIの既定server URL、さらにmountするアプリへのprefixも引き継ぎます。
prefixは信頼できるサーバー設定から渡してください。`X-Forwarded-Prefix`を自動では信頼しません。
WSGIの`PATH_INFO`はdecode済みなので、元のencoded slashなどを完全復元することはできません。
WSGIで正規化済みの入力headerも元の重複やunderscoreを復元できません。曖昧な入力の拒否は
WSGIサーバー／proxyでも設定してください。

ASGIの重複headerは`request.raw_headers`へ保持します。分割Cookieは結合して解析し、
応答の複数`Set-Cookie`は別headerのまま送ります。Content-Lengthとhop-by-hop headerは
gateway側で正規化し、HEAD・204/205/304ではbodyを送りません。

標準ASGI middlewareは`app.asgi_app`の外側へ、WSGI middlewareは`app.wsgi_app`の外側へ置けます。
Higuma独自middlewareとは呼び出し形式が異なります。独自middlewareの`call_next`は同期APIであり、
ASGI loopから直接呼び出すasync middleware chainには使いません。非同期のチェーン処理は
標準ASGI middlewareとして入口の外側に実装してください。Flask/Starletteの内部APIに依存する拡張の
完全互換を意味しません。[Flask互換性](flask-compat.md)も参照してください。

## 保持する制約

- request bodyはbuffer済みで、uploadの逐次読取APIは追加していません。
- responseはchunkごとに送信します。ASGIでは`send()`を待ち、fileはPython側でchunk読取します。
  Rust HTTP server経由と同じ性能・gzip・zero-copyを保証しません。
- `mount_wsgi` / `mount_asgi`は従来どおりbuffered HTTP連携です。外部ASGI起動しても、
  mount先のWS・lifespan・無期限streamが自動対応するわけではありません。
- teardownはdispatch終了時です。遅延stream内ではteardownで閉じた`g`上のresourceを使いません。
  yield dependencyのcleanupはresponseのclose・background処理後です。
- background task・session Cookie・rate limit・SQLite等の既存制約は変わりません。
- TLS、HTTP/2/3、圧縮、proxy trust、worker管理、reloadは外部サーバー／proxyの責任です。
  組み込みサーバー用CLI optionがそのまま外部サーバーへ適用されることはありません。

仕様: [ASGI](https://asgi.readthedocs.io/en/stable/specs/main.html)、
[lifespan](https://asgi.readthedocs.io/en/stable/specs/lifespan.html)、
[WSGI PEP 3333](https://peps.python.org/pep-3333/)。
