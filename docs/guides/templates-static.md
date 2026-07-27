# SSRと静的ファイル

## テンプレートを配置する

標準ではapplication rootの `templates/` を読み込みます。

```text
myapp/
├── app.py
├── templates/
│   ├── base.html
│   └── index.html
└── static/
    └── app.css
```

```python
app = Higuma(__name__, template_folder="templates")
```

## 描画する

```python
@app.get("/")
def index():
    return app.render_template(
        "index.html",
        title="higuma",
        features=["Rust core", "Python API", "SSR"],
    )
```

```html title="templates/index.html"
{% extends "base.html" %}

{% block content %}
  <h1>{{ title }}</h1>
  <ul>
    {% for feature in features %}
      <li>{{ feature }}</li>
    {% endfor %}
  </ul>
{% endblock %}
```

MiniJinjaがRust側で描画し、`text/html; charset=utf-8` として返します。

## Context processor

すべてのtemplateへ共通値を追加できます。

```python
@app.context_processor
def inject_globals():
    return {"site_name": "My higuma app"}
```

## 静的ファイル

`static_folder` と `static_url_path` を指定できます。

```python
app = Higuma(
    __name__,
    static_folder="static",
    static_url_path="/assets",
)
```

```html
<link rel="stylesheet" href="/assets/app.css">
```

静的ファイルではMIME type、`Content-Length`、ETag、`If-None-Match`、
`Last-Modified`、byte Rangeを処理します。path traversalは拒否されます。

!!! warning "Content-Type"
    HTMLを文字列以外のraw bytesで返す場合は、`HTMLResponse` または
    `Response(..., content_type="text/html; charset=utf-8")` を指定してください。
