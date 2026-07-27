# インストールと起動

## 必要環境

- Python 3.10以上

PyPIからwheelをインストールする場合、RustやC/C++ build toolsは不要です。
ソースからbuildする場合のみRust stableと対応するリンカーが必要です。

## インストール

=== "PyPI"

    ```bash
    python -m pip install higuma
    ```

    公開パッケージは
    [pypi.org/project/higuma](https://pypi.org/project/higuma/)
    で確認できます。

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

## 対応プラットフォーム

`0.1.0` はCPython stable ABIを使うため、1つのwheelでPython 3.10以降に対応します。

| OS | Architecture |
|---|---|
| Windows | x86_64 |
| Linux glibc | x86_64、aarch64 |
| Linux musl / Alpine | x86_64 |
| macOS | Intel x86_64、Apple Silicon arm64 |

対応wheelがない環境ではsdistからbuildされるため、Rust toolchainが必要です。

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
