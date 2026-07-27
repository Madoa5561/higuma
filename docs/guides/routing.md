# ルーティング

## 基本

HTTPメソッドごとのdecoratorと、複数メソッドを指定する `route()` を利用できます。

```python
@app.get("/articles")
def list_articles():
    return {"items": []}


@app.post("/articles")
def create_article():
    return {"created": True}, 201


@app.route("/health", methods=["GET", "HEAD"])
def health():
    return "ok"
```

`GET` には `HEAD` が自動で対応します。`OPTIONS` も自動応答し、`Allow` headerを返します。

## 動的パラメータ

| Converter | 例 | Python型 |
|---|---|---|
| `string` / `str` | `<string:name>` | `str` |
| `int` | `<int:user_id>` | `int` |
| `float` | `<float:score>` | `float` |
| `uuid` | `<uuid:item_id>` | `UUID` |
| `path` | `<path:filename>` | `str` |

```python
@app.get("/users/<int:user_id>")
def user(user_id: int):
    return {"id": user_id}
```

静的ルートは動的ルートより優先されます。URL pathはpercent decodeされ、不正な値はマッチしません。

## URL生成

endpoint名とpath parameterからURLを生成します。

```python
@app.get("/users/<int:user_id>", endpoint="user_detail")
def user(user_id):
    return {"id": user_id}


url = app.url_for("user_detail", user_id=42)
# /users/42
```

## Blueprint

機能単位でrouteを分割できます。

```python title="admin.py"
from higuma import Blueprint

admin = Blueprint("admin", __name__, url_prefix="/admin")


@admin.get("/")
def dashboard():
    return "Dashboard"
```

```python title="app.py"
from admin import admin

app.register_blueprint(admin)
```

endpointは `admin.dashboard` のようにBlueprint名でnamespace化されます。

## エラー

存在しないURLは404、URLはあるがmethodが異なる場合は405です。

```python
from higuma import NotFound, abort


@app.get("/private")
def private():
    abort(404, "Not found")


@app.errorhandler(NotFound)
def not_found(error):
    return {"error": error.description}, 404
```
