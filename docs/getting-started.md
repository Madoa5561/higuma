# インストールと起動

## 必要環境

- Python 3.10以上
- Rust stable
- 対応するC/C++リンカー

WindowsではVisual Studio Build ToolsのMSVC、LinuxではGCCまたはClang、macOSでは
Xcode Command Line Toolsを利用します。

## インストール

=== "PyPI"

    ```bash
    python -m pip install higuma
    ```

=== "開発版"

    ```bash
    git clone https://github.com/Madoa5561/higuma.git
    cd higuma
    python -m pip install -U pip
    python -m pip install -e .
    ```

=== "開発ツール込み"

    ```bash
    python -m pip install -e ".[dev]"
    ```

WindowsでPythonとRustのarchitectureが異なる場合は、先にtargetを追加します。

```powershell
rustup target add x86_64-pc-windows-msvc
```

## アプリを作る

```python title="app.py"
from higuma import Higuma, request

app = Higuma(__name__)


@app.get("/")
def home():
    return "<h1>It works</h1>"


@app.post("/api/echo")
def echo():
    return {"received": request.json}
```

## 起動する

=== "Python"

    ```bash
    python app.py
    ```

    `app.py`の末尾に次を追加します。

    ```python
    if __name__ == "__main__":
        app.run(host="127.0.0.1", port=8000)
    ```

=== "CLI"

    ```bash
    higuma run app:app --host 127.0.0.1 --port 8000
    ```

開発サーバーは `http://127.0.0.1:8000` で待ち受けます。

## テストする

ネットワークを起動しない高速なテストクライアントを利用できます。

```python title="test_app.py"
from app import app


def test_home():
    client = app.test_client()
    response = client.get("/")
    assert response.status_code == 200
    assert "It works" in response.text
```

```bash
python -m pytest
```

## 次に読む

- [ルーティング](guides/routing.md)
- [RequestとResponse](guides/requests-responses.md)
- [SSRと静的ファイル](guides/templates-static.md)
