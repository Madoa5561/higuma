---
hide:
  - navigation
  - toc
---

<div class="hero">
  <div class="hero__eyebrow">PYTHON ERGONOMICS. RUST VELOCITY.</div>
  <h1>Pythonらしく書く。<br><span>Rustの速度で届ける。</span></h1>
  <p>
    higumaは、Flaskに近い使い心地と、axum・TokioによるRust HTTPコアを組み合わせた
    SSR対応Python Webフレームワークです。
  </p>
  <div class="hero__actions">
    <a class="md-button md-button--primary" href="getting-started/">5分で始める</a>
    <a class="md-button" href="https://github.com/Madoa5561/higuma">GitHubで見る</a>
  </div>
</div>

<div class="install-command">
  <span>$</span>
  <code>pip install higuma</code>
  <a href="https://pypi.org/project/higuma/">PyPI 0.1.0</a>
</div>

<div class="feature-grid">
  <article>
    <strong>01</strong>
    <h2>Flaskライク</h2>
    <p><code>@app.get()</code>、Blueprint、Request/Response、hooks。学習コストを抑えたAPIです。</p>
  </article>
  <article>
    <strong>02</strong>
    <h2>Rust HTTPコア</h2>
    <p>axumとTokioが接続、ルーティング、body制限、HEAD/OPTIONSを処理します。</p>
  </article>
  <article>
    <strong>03</strong>
    <h2>SSR First</h2>
    <p>MiniJinjaテンプレートをRust側で描画。HTMLを正しいContent-Typeで返します。</p>
  </article>
</div>

## 最小のアプリ

```python title="app.py"
from higuma import Higuma

app = Higuma(__name__)


@app.get("/")
def index():
    return "<h1>Hello from higuma</h1>"


@app.get("/users/<int:user_id>")
def user(user_id: int):
    return app.jsonify(user_id=user_id)


if __name__ == "__main__":
    app.run()
```

```bash
python app.py
```

ブラウザで `http://127.0.0.1:8000` を開けば起動を確認できます。

## なぜhigumaか

Pythonのハンドラや豊富なエコシステムはそのままに、HTTPサーバーとして頻繁に通る処理を
Rustへ寄せる設計です。すべてをRustにするのではなく、Pythonで開発しやすい境界を保ちます。

!!! info "現在のステータス"
    `0.1.0` はalphaリリースです。主要なWebアプリケーション機能を備えていますが、
    公開サービスへ採用する際は要件に応じた負荷試験とセキュリティレビューを行ってください。

<div class="next-step">
  <span>NEXT</span>
  <div>
    <h2>最初のアプリを起動する</h2>
    <p>インストール、ルーティング、テストまでを順番に進めます。</p>
  </div>
  <a href="getting-started/">はじめる →</a>
</div>
