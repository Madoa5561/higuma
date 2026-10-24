# Flask互換性とエコシステム

higumaはFlaskに着想を得た独立したフレームワークです。Flask拡張をそのまま
読み込めるdrop-in replacementではありません。既存のFlaskアプリはWSGI mountで
動かし、新しい部分をhigumaのrouteで書く段階的な移行ができます。

0.8.0からは逆方向に、Higumaを`app.asgi_app`／`app.wsgi_app`として外部サーバーへ渡せます。
[サーバーインターフェース](server-interfaces.md)にHTTP・WS・lifespan・workerの対応範囲をまとめています。
WSGI対応はFlask内部API／extensionの完全互換を意味しません。

## 既存のFlaskアプリを接続する

```bash
python -m pip install "higuma[compat]"
higuma run examples.flask_mount:create_app
```

repository外では、自分のmodule名を指定してください。`compat`は任意のFlask 3.1系
検証用依存です。higuma単体のPython runtime依存は引き続き0です。

```python
from flask import Flask, url_for
from higuma import Higuma

legacy = Flask(__name__)

@legacy.get("/hello/<name>")
def hello(name):
    return {"name": name, "url": url_for("hello", name=name)}

app = Higuma(__name__)
app.mount_wsgi("/legacy", legacy)
```

`/legacy/hello/%E6%97%A5%E6%9C%AC`を開くと日本語のpath parameterを受け取れます。
Flask側の`url_for`には`/legacy`が含まれ、Cookie、JSON body、redirectも渡されます。
mount内ではFlaskの`request`・`session`を使い、higumaの同名proxyと混同しないでください。
Flask拡張はFlask側へ初期化し、個別に動作を検証します。

## higumaで使うFlask風API {#flask-style-api}

| API | 動作 |
| --- | --- |
| `with app.app_context():` | `current_app`と`g`を有効化。nested context終了時に外側を復元 |
| `g.name` / `g.get/pop/setdefault` | アプリコンテキスト単位の値。通常はリクエストごとに独立 |
| `app.extensions` | extensionのアプリ単位の保存先。リクエスト固有値は保存しない |
| `@app.teardown_appcontext` | context終了時に逆順実行。引数は未処理例外または`None` |
| `has_app_context()` / `has_request_context()` | contextの存在確認 |
| `with app.test_request_context(path, **kwargs):` | 0.7+: dispatchせずrequestとapp contextを有効化。[テストガイド](testing.md) |
| `with app.request_context(request):` | 0.7+: Higumaの`Request`からcontextを作成 |
| `@app.teardown_request` | 0.7+: request contextが有効なまま逆順でcleanup |
| `@after_this_request` | 0.7+: 今回のrequestだけのresponse callback。登録順に実行 |
| `Blueprint.before_request/after_request/teardown_request` | 0.7+: 選択されたBlueprintだけのhooks |
| `Blueprint.errorhandler/register_error_handler/context_processor` | 0.7+: Blueprint限定のエラー処理とtemplate値 |
| `session[key]` | `SessionMiddleware`の署名Cookie sessionへのproxy |
| `flash(message, category="message")` | 次のrequestへメッセージを保存 |
| `get_flashed_messages(with_categories=False, category_filter=())` | sessionから一度取り出す。同一request内では結果を再取得可能 |
| `url_for(endpoint, **values)` | path値、余剰query値、list値、`_anchor`、`_external`、`_scheme` |
| `request.endpoint` / `request.blueprint` | 選択されたendpointとBlueprint名。未選択時は`None` |
| `app.register_error_handler(code_or_exception, handler)` | decoratorを使わないerror handler登録 |
| `render_template(...)` | app context内ではcontext processorを適用 |
| `response.headers` | 読み書き・削除・get/popのheader名は大文字小文字を区別しない |
| `request.values/is_json/mimetype/host_url/full_path` | 0.7+: query優先の入力統合とMIME・URL helper |
| `response.data/get_data/set_data/is_json/get_json` | 0.7+: buffered bodyの読み書き、MIMEを確認したJSON読取 |
| `higuma run module:create_app()` | 引数なしfactory。任意式の評価はしない |

相対endpointの`url_for(".detail", id=1)`はBlueprint request内で使用できます。
余剰query値の`None`は省略します。`_scheme`は`_external=True`が必要です。
request外の外部URL生成には`app.config["SERVER_NAME"]`を設定してください。
外部URLにrequestのHostを使う場合、本番では`TrustedHostMiddleware`を設定します。

## sessionとflashの例

```python
import os
from higuma import (
    CSRFProtection, Higuma, SessionMiddleware, csrf_token,
    flash, get_flashed_messages, session,
)

app = Higuma(__name__)
app.add_middleware(SessionMiddleware, os.environ["HIGUMA_SECRET_KEY"])
app.add_middleware(CSRFProtection)

@app.get("/")
def index():
    return {"messages": get_flashed_messages(), "csrf_token": csrf_token()}

@app.post("/save")
def save():
    session["visits"] = session.get("visits", 0) + 1
    flash("保存しました", "success")
    return {"ok": True}
```

最初のGETでCookieとtokenを取得し、POSTの`X-CSRF-Token` headerへtokenを送ります。
Cookieは署名されますが暗号化されません。秘密情報や大きなflash本文を保存しないでください。
本番HTTPSでは`SessionMiddleware(..., secure=True)`を設定します。
実行例は`examples/flask_compat.py`です。

## Blueprintとrequest lifecycle（0.7.0以降）

```python
from higuma import Blueprint, Higuma, after_this_request, g

app = Higuma(__name__)
pages = Blueprint("pages", __name__, url_prefix="/pages")

@pages.before_request
def prepare():
    g.section = "pages"

@pages.after_request
def scoped_headers(response):
    response.headers["X-Section"] = g.get("section", "pages")
    return response

@pages.teardown_request
def cleanup(error):
    g.pop("section", None)

@pages.get("/")
def index():
    @after_this_request
    def local(response):
        response.headers["X-Local"] = "yes"
        return response
    return {"section": g.section}

app.register_blueprint(pages)
```

通常のHTTP requestではapp before → Blueprint before → view → request-local after
→ Blueprint after → app after → session保存 → Blueprint teardown → app teardownの順です。
beforeは登録順、afterとteardownは逆順、`after_this_request`は登録順です。
callbackは同期・非同期を使えます。after callbackはresponseを返します。
middlewareやbefore hookの早期応答時にはviewや一部beforeが実行されないため、
after/teardownは未初期化の`g`にも対応させます。teardownからsessionを変更しても保存されません。

Blueprintのerror handlerはそのrouteが選択された場合だけ有効です。
view内の`abort(404)`は処理できますが、未知のURLの404やroutingの405はapp側で処理します。
HTTP status codeのhandlerを優先し、同じcodeならBlueprint → app、次に例外型を探索します。
context processorはapp → Blueprintの順に適用し、明示したtemplate値を最優先します。
`app.update_template_context(values)`でも適用できます。Blueprint名とendpointには
空文字・ドット入りBlueprint名やドット入りlocal endpointを使わず、登録名を重複させません。

`after_this_request`や`after_request`内のsession更新は、最終responseが決まった後に
Cookieへ保存されます。responseを差し替えても`Vary: Cookie`とsession Cookieを保持します。
CSRF付きの実行例は`examples/flask_lifecycle.py`、手順は[Examples](examples.md#request-lifecycle)です。

## JSONと値の選択

`request.values`はGETではqueryだけ、それ以外ではquery → formの順です。
同名keyではqueryを優先し、`getlist()`にはqueryとform両方の値を含めます。
Higuma既存のMultiDictは、同じ入力元でkeyが重複した場合は最後の値を選びます。
Flask/Werkzeugの最初の値とは異なるため、重複が意味を持つ入力は`getlist()`で扱います。

JSON MIMEは`application/json`または`application/*+json`です。requestのJSONはbytesから
解析し、UTF-8/16/32に対応します。不正なUnicodeやJSONは400、JSON以外のMIMEは415です。
`silent=True`で失敗しても、その後の通常読取でエラーを隠しません。

0.7.0からresponseの`get_json()`/`.json`はMIMEも確認し、JSON以外なら`None`になります。
従来どおり非JSON MIMEのbodyを解析する必要がある場合は`get_json(force=True)`を指定します。
解析失敗を`None`にするには`silent=True`を指定します。responseの解析結果はcacheしません。

## 明示的な境界

- Flaskのsignals、Werkzeug routing、nested Blueprint、Flask CLI extensionなどの
  完全互換は提供しません。Flask extensionの`init_app(higuma_app)`動作は保証しません。
- `request_context`はWSGI environではなくHigumaの`Request`を受け取ります。
  context helperは`with`用で、Flaskのpush/pop wrapperではありません。詳細は[テスト](testing.md)。
- `request.query_string`はstr、`request.base_url`は従来どおりorigin rootです。
  `host_url`もorigin root、`full_path`はpathとquery（空queryでも末尾`?`付き）です。
  Flaskの`base_url`はpathを含むため異なります。`request.path`は元のpercent-encoded pathで、
  path引数はdecodeされます。末尾slashの扱いもWerkzeug routingとは異なります。
- request bodyはbuffer済みです。`get_json(cache=...)`、Flaskのbody stream API、
  完全なWerkzeug request/response操作を提供するものではありません。
- Template engineはMiniJinjaです。Flask/Jinja2の全filterやPython callable globalは
  提供しません。URLやflashはviewで生成しtemplateのcontextへ渡します。
- WSGI/ASGI mountはHTTP request/responseをbufferします。mount内のSSE・無期限stream、
  WebSocket、ASGI lifespanの代替ではありません。streamはhigumaのresponse APIを使い、
  組み込みまたは対応する外部サーバーで実行します。
- ASGIはdecoded `path`と元のpercent-encoded `raw_path`を渡します。
- WSGI header正規化による衝突を避けるため、名前に`_`を含む入力headerは破棄します。
- teardownはdispatch/context終了時に実行されます。遅延streamやbackground taskで
  teardown済みの`g`上のDB接続などを使わず、その処理の中でresourceを開いてください。
- RateLimitMiddlewareはprocess内のみの制限です。`max_keys`到達時は新しいidentityで
  共有枠を使い、有効な履歴は追い出しません。共有枠の履歴失効までは個別枠へ移行しません。
  混雑時には新規利用者が同じ制限枠になります。複数workerの一括制限はproxy等で行います。

仕様の参考: [Flask application context](https://flask.palletsprojects.com/en/stable/appcontext/)、
[WSGI PEP 3333](https://peps.python.org/pep-3333/)、
[ASGI HTTP scope](https://asgi.readthedocs.io/en/stable/specs/www.html)。
