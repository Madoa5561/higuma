# higuma

Rust HTTPコアとSSRを備えた、FlaskライクなPython Webフレームワークです。

- [日本語ドキュメント](https://higuma.moyashi.xyz/)
- [English documentation](https://higuma.moyashi.xyz/en/)
- [PyPI](https://pypi.org/project/higuma/)
- [Examples](https://github.com/Madoa5561/higuma/tree/main/examples)

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
    return {"message": "Hello from higuma"}


if __name__ == "__main__":
    app.run()
```

```bash
python app.py
```

ブラウザで <http://127.0.0.1:8000> を開きます。

## 開発者

- [madoa5561](https://github.com/Madoa5561)

## ライセンス

[MIT License](LICENSE)
