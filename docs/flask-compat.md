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

## 0.11: URL hooks・再利用部品・設定読込

この節は0.11.0の仕様です。`python -m pip install "higuma==0.11.0"`で利用します。
repository rootの開発用には`python -m pip install -e .`も使えます。必須Python依存は0のままです。

### 言語やtenantなどのURL値をまとめて扱う

`@app.url_value_preprocessor`／`@blueprint.url_value_preprocessor`は
`(endpoint, values)`を受け取り、before hooksより先にapp → 親Blueprint → 子Blueprintの順で
実行します。`values`は`request.view_args`と同じdictionaryです。`pop`した値はviewへ渡さず、
`g`へ移せます。未知のURLや405ではappのcallbackに`(None, None)`を渡します。
返り値で早期応答はせず、必要なら`abort()`またはbefore hookを使います。

`@app.url_defaults`／`@blueprint.url_defaults`も`(endpoint, values)`を受け取り、
`url_for()`の値を同じapp → 親 → 子の順で補完します。明示値を壊さないよう`setdefault`を使います。
`app.inject_url_defaults(endpoint, values)`で直接補完することもできます。
`request.blueprints`は子から親へ向かう登録名のlistです。

```python
from higuma import Blueprint, Higuma, g, url_for

app = Higuma(__name__)
pages = Blueprint("pages", __name__, url_prefix="/<language>")

@pages.url_value_preprocessor
def select_language(endpoint, values):
    g.language = values.pop("language")

@pages.url_defaults
def default_language(endpoint, values):
    values.setdefault("language", g.get("language", "ja"))

@pages.get("/hello/<name>")
def hello(name):
    return {"language": g.language, "next": url_for(".hello", name=name)}

app.register_blueprint(pages)
```

URL callbackはFlaskと同じく**同期関数**です。async関数・awaitableの返却は拒否します。
HTTP requestの機能で、WebSocket認証前処理には適用しません。
URL前処理を使うASGI routeは、hookを飛ばさないよう互換経路で実行します。
request contextだけを作るhelperは、従来どおり前処理をdispatchしません。
URL値をユーザー識別に使う場合も、認証・認可・許容値の検証は別途必要です。

### Blueprintからアプリ全体の機能を登録する

`before_app_request`／`after_app_request`／`teardown_app_request`、
`app_context_processor`、`app_errorhandler`、`app_url_value_preprocessor`、
`app_url_defaults`をBlueprintに追加できます。これらはBlueprint外のrouteにも適用します。
同じBlueprint objectを別名で複数回登録しても、全体向けcallbackは**アプリごとに一度だけ**登録します。
別のアプリに登録した場合は独立して適用します。アプリへの登録前にcallbackを定義してください。
全体向けhooksの順序は、アプリへ取り込まれた時点の登録順と通常のbefore/after/teardown規則に従います。
`app_errorhandler(404)`は未知のURLも扱いますが、従来の`errorhandler(404)`は選択された部品内のみです。

### 設定の読込と抽出

```python
import json

app.config.from_file("settings.json", load=json.load, silent=True)
app.config.from_object("yourproject.settings.Development")
app.config.from_prefixed_env("MYAPP")
client_options = app.config.get_namespace("CLIENT_")
```

- `from_file(filename, load=json.load, silent=False, text=True)`はアプリの`root_path`を基準に
  相対パスを解決します。従来のcwd相対の呼び出しは絶対パスを渡して移行してください。
  `text=False`はbinary loader用です。loaderはmappingを返します。
- `silent=True`は存在しないパス等だけを無視します。不正JSON・loader失敗・権限エラーは隠しません。
  `from_envvar(name, silent=True)`も参照先が存在しない場合に`False`を返します。
- `from_object`はmoduleだけでなく`module.Class`を受け取り、大文字属性だけを読み込みます。
- `from_prefixed_env(prefix="HIGUMA", loads=json.loads)`は名前順に適用します。
  例: `MYAPP_CLIENT__TLS__ENABLED=true` → `CLIENT["TLS"]["ENABLED"] = True`。
  `__`を含むキーは階層として解釈するため、以前のflat keyを使う場合は`from_mapping`で明示設定します。
  途中の値がdictでない場合はエラーにし、黙って置き換えません。decoderが失敗した値はstrのままです。
- `get_namespace("CLIENT_")`は接頭辞を除き、小文字keyの新しいdictを返します。
  `lowercase=False`／`trim_namespace=False`で変更できます。深いcopyではありません。
- `from_mapping`と`from_file`は従来の小文字keyも保持します。Flaskの大文字key限定とは異なります。
  設定値が全て自動適用されるわけではありません。body上限などの設定時点は各APIの仕様に従います。

Python設定ファイルの実行、`.env`の自動読込、instance folder APIは追加していません。
loader・import対象・filenameは開発者が管理し、requestの入力を渡さないでください。
`Config`は`from higuma import Config`でもimportできます。

### JSONとresponse helperの修正

- `jsonify()`／`jsonify(None)`はJSON `null`です。以前の空objectが必要なら`jsonify({})`へ変更します。
  複数位置引数はarray、keywordだけならobjectです。位置引数とkeywordの混在は`TypeError`です。
- `app.jsonify(*values, status=..., headers=...)`にも反映します。`status`／`headers`は従来どおり
  response指定用のHiguma拡張です。同名のJSON fieldにはdictまたはmoduleの`jsonify`を使います。
- `make_response()`は空の200 responseを返し、`(body, {header: value})`も受け取ります。
  Werkzeugのresponse classやstatus文字列、重複headerの全操作を互換化するものではありません。
- `request.get_json(cache=False)`は解析済みJSONを再利用・更新せず、受信済みbodyを再解析します。
  bodyそのものを破棄・再受信する指定ではありません。Flaskの未cache stream消費とは異なります。

repository rootでの依存追加なしの実行例: `python -m examples.flask_migration`。
GET `/ja/hello/bear`、GET `/`、JSON付きPOST `/echo`を試せます。
Flaskとの比較テストと、組み込み／ASGI／WSGIの実通信テストで挙動を確認しています。

## 明示的な境界

0.9+では親子Blueprint・部品のtemplate/static・独自CLIを追加しています。
[開発体験ガイド](developer-experience.md)に継承順と互換範囲をまとめています。

- 0.10+の[Flask風Signals](operations.md)は依存0の独自実装です。Flask/Blinkerのsignal object、Werkzeug routing、Flask CLI extensionなどの
  完全互換は提供しません。Flask extensionの`init_app(higuma_app)`動作は保証しません。
- `request_context`はWSGI environではなくHigumaの`Request`を受け取ります。
  context helperは`with`用で、Flaskのpush/pop wrapperではありません。詳細は[テスト](testing.md)。
- `request.query_string`はstr、`request.base_url`は従来どおりorigin rootです。
  `host_url`もorigin root、`full_path`はpathとquery（空queryでも末尾`?`付き）です。
  Flaskの`base_url`はpathを含むため異なります。`request.path`は元のpercent-encoded pathで、
  path引数はdecodeされます。末尾slashの扱いもWerkzeug routingとは異なります。
- 入力は受信後dispatchし、0.9+では[spoolと逐次読取](uploads.md)を使えます。Flask/Werkzeugのbody API全体、
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
