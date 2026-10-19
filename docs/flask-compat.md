# Flask互換性とエコシステム

higumaはFlaskに着想を得た独立したフレームワークです。Flask拡張をそのまま
読み込めるdrop-in replacementではありません。既存のFlaskアプリはWSGI mountで
動かし、新しい部分をhigumaのrouteで書く段階的な移行ができます。

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
| `session[key]` | `SessionMiddleware`の署名Cookie sessionへのproxy |
| `flash(message, category="message")` | 次のrequestへメッセージを保存 |
| `get_flashed_messages(with_categories=False, category_filter=())` | sessionから一度取り出す。同一request内では結果を再取得可能 |
| `url_for(endpoint, **values)` | path値、余剰query値、list値、`_anchor`、`_external`、`_scheme` |
| `request.endpoint` / `request.blueprint` | 選択されたendpointとBlueprint名。未選択時は`None` |
| `app.register_error_handler(code_or_exception, handler)` | decoratorを使わないerror handler登録 |
| `render_template(...)` | app context内ではcontext processorを適用 |
| `response.headers` | 読み書き・削除・get/popのheader名は大文字小文字を区別しない |
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

## 明示的な境界

- Flaskのsignals、Werkzeug routing、Flask CLI extension、`test_request_context`などの
  完全互換は提供しません。Flask extensionの`init_app(higuma_app)`動作は保証しません。
- Template engineはMiniJinjaです。Flask/Jinja2の全filterやPython callable globalは
  提供しません。URLやflashはviewで生成しtemplateのcontextへ渡します。
- WSGI/ASGI mountはHTTP request/responseをbufferします。mount内のSSE・無期限stream、
  WebSocket、ASGI lifespanの代替ではありません。streamはhiguma native APIを使います。
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
