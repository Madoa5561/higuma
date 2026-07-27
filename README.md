# higuma

Rust HTTPコアとSSRを備えた、FlaskライクなPython Webフレームワークです。

- [公式ドキュメント](https://higuma.moyashi.xyz)
- [PyPI](https://pypi.org/project/higuma/)

## インストール

Python 3.10以上で利用できます。

```bash
pip install higuma
```

## 簡単な例

```python
from higuma import Higuma

app = Higuma(__name__)


@app.get("/")
def index():
    return "<h1>Hello from higuma</h1>"


@app.get("/users/<int:user_id>")
def user(user_id: int):
    return {"user_id": user_id}


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
```

```bash
python app.py
```

`http://127.0.0.1:8000` をブラウザで開きます。

## 開発者

- [madoa5561](https://github.com/Madoa5561)

## ライセンス

[MIT License](LICENSE)
