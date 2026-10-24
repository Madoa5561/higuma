# テストとrequest context

Higuma 0.7.0以降のhelperを含みます。通常のview・middleware・CSRF・sessionはtest clientで、
contextに依存する小さな関数は`test_request_context`で、通信仕様は実serverで検証します。

## Test clientでdispatchする

```python
from higuma import Higuma, request

app = Higuma(__name__, static_folder=None)

@app.post("/echo")
def echo():
    return {"value": request.get_json(), "secure": request.scheme == "https"}

client = app.test_client()
response = client.post("/echo", json={"name": "bear"}, base_url="https://example.test")
assert response.status_code == 200
assert response.is_json
assert response.get_json() == {"value": {"name": "bear"}, "secure": True}
```

`get/post/put/patch/delete/options/head`と`open(path, method="GET", ...)`を使えます。
responseの`get_data(as_text=True)`は文字列、`.data`/`get_data()`はbytesを返します。

| 引数 | 動作 |
| --- | --- |
| `headers` | header mapping。名前は大文字小文字を区別しない |
| `query` | mappingまたはpair list。list値は繰り返しqueryに展開 |
| `query_string`（0.7+） | str、ASCII bytes、mapping、pair list。`query`との併用不可 |
| `base_url`（0.7+） | HTTP(S) originのみ。例: `https://example.test:8443`。path prefixや認証情報は不可 |
| `content_type`（0.7+） | Content-Typeを指定 |
| `json` | JSON化する値。非Noneの値は`data/files`と併用不可 |
| `data` | str、bytes、form mapping。mappingのlist値は繰り返しform値 |
| `files` | `{"file": ("bear.txt", b"hello", "text/plain")}`。同名複数fileはtupleのlist |
| `follow_redirects` | 既定False。同じhostの相対/絶対HTTP(S) redirectだけ追跡 |
| `max_redirects` | 既定20。loopや上限超過は`RuntimeError` |

`query`/`query_string`を渡すとpath内のqueryを置き換えます。`json=None`はbodyなしです。
JSON nullを送る場合は`data="null", content_type="application/json"`にします。
303はGET（HEADはHEAD）へ、301/302のGET/HEAD以外もGETへ変更します。307/308はmethodとbodyを保持します。
最終responseの`.history`は途中のresponseのtupleです。外部hostへ通信しません。

## request contextだけを作る

```python
from higuma import Higuma, has_request_context, request, url_for

app = Higuma(__name__, static_folder=None)

@app.get("/bears/<int:bear_id>")
def bear(bear_id):
    return {"id": bear_id}

with app.test_request_context("/bears/7", query_string={"name": "higuma"}) as current:
    assert request.view_args == {"bear_id": 7}
    assert request.endpoint == "bear"
    assert current.args["name"] == "higuma"
    assert url_for("bear", bear_id=9) == "/bears/9"
assert not has_request_context()
```

このhelperは上表のrequest構築引数（`method`を含む）を受け取り、routing metadataと
型付きpath値を設定します。`follow_redirects/max_redirects`はdispatch専用なので受け取りません。
`with`が返す値はHigumaの`Request`です。FlaskのRequestContext wrapperやpush/pop APIではありません。

contextを作るだけではview、middleware、before/after hook、CSRF、session読み込みは実行しません。
`SessionMiddleware`が走らないため、sessionを使うテストはtest clientからrequestします。
終了時には`teardown_request`を実行します。独自app contextを作成した場合だけ、
`teardown_appcontext`も実行します。nested context終了後は外側のrequestと`g`を復元します。

直接作成したrequestにもcontextを適用できます。

```python
from higuma import Request

with app.request_context(Request({"path": "/manual", "method": "GET"})):
    assert request.path == "/manual"
```

`request_context`はWSGI environを受け取りません。また自動でrouteを選択しません。
route情報が必要なら`test_request_context`を使います。

## Lifespanとsessionを含むテスト

`with app.test_client() as client:`はstartup/shutdown・lifespanを実行します。
Flaskのclient contextと異なり、最後のrequest contextを保持しません。
request終了後はresponseをassertし、request proxyや`g`を参照しません。

session・CSRF・Blueprint hooksをまとめて試すには、
`examples/flask_lifecycle.py`の[実行手順](examples.md#request-lifecycle)を参照してください。
Cookie jarは簡易な名前と値の保存であり、browserのDomain/Path/Secure/SameSite・期限判定を
完全再現しません。HTTPS、CORS、browserのCookie制約は実browserでも検証します。

## repositoryの回帰テスト

外部サーバー入口の検証は次を使います。`app.test_client()`だけではASGI／WSGIの
protocolやtransportを通らないため、それだけで外部サーバー対応の確認にはなりません。

```bash
python -m pytest tests/test_gateways.py tests/test_gateways_server.py
```

protocol testは標準ライブラリで実行できます（Flask mount比較は任意の`compat` extra）。
実通信では組み込み・Uvicorn・wsgirefへ同じアプリを起動し、HTTP／SSE／WSを検証します。
Uvicorn／WS clientのcaseには`asgi` extraが必要です。CIではextra付きで実行します。
WSGIの標準WebSocket非対応もHTTPテストで固定します。

```bash
python -m pytest tests/test_flask_helpers.py tests/test_flask_helpers_server.py
python -m pytest
```

`compat` extraがある場合は同じ共通動作を実Flaskにも実行します。未導入ならFlaskのcaseのみskipします。
server testは別processでRust serverを起動し、CSRF拒否、Unicode JSON、session Cookie、
Blueprint response header、teardownを実HTTPで確認します。終了時にprocessを停止します。

test clientはstream/file/templateをメモリへ展開します。streamingのtiming、切断、backpressure、
WebSocket handshake、proxy、TLSはtest clientだけでは保証できません。対応する実socket testと
公開wheelのfresh install smokeを併用します。
