# 大容量入力とアップロード（0.9+）

native・ASGI・WSGIで受信bodyを分割して読み、既定1MiBを超えると一時ファイルへ退避します。
body全体の上限は従来どおり既定8MiBです。メモリ閾値と許容容量は別の設定です。

```python
from higuma import Higuma, request

app = Higuma(__name__, max_content_length=64 * 1024 * 1024,
             request_memory_limit=1024 * 1024)

@app.post("/upload")
def upload():
    size = sum(len(chunk) for chunk in request.iter_body())
    return {"size": size, "spooled": request.body_spooled}
```

## 読み取りAPI

- `request.stream`: rewind可能なbinary file-like。`read(size)` / `seek` / `tell`を使えます。
- `iter_body(chunk_size=65536)`: 現在位置から逐次読取。再読取時は`stream.seek(0)`。
- `aiter_body(chunk_size=65536)`: async版。保存済みbodyを読み、disk I/Oをloopの外で行います。
- `body_size` / `content_length`: 受信した実サイズ。metadataの取得だけで全量展開しません。
- `body_spooled`: 入力bodyがdiskへ退避されたかどうか。個々のmultipart fileとは別です。
- `body` / `get_data` / `text` / `json`: 互換性を維持しますが、利用時には全量をメモリへ展開できます。

**受信完了後にdispatchする設計は維持します。** 上のiteratorは保存済み入力の逐次読取で、
クライアントのアップロード途中からhandlerへ流すlive network streamではありません。
それが必要なら外部ASGIアプリへ直接処理を分担し、buffered mountを間に置かないでください。

## Multipart

```python
@app.post("/file")
def file():
    upload = request.files["file"]
    total = 0
    while chunk := upload.read(65536):
        total += len(chunk)
    return {"filename": upload.filename, "size": total}
```

multipartのboundaryをchunkをまたいで解析し、fileは個別にspoolします。
`UploadFile.read/seek/save/getvalue`は維持します。`getvalue`は全量読取です。
filenameは`secure_filename`で正規化されますが、同名上書きや保存先の権限はアプリの責任です。
`save`は既存ファイルを上書きできるため、永続保存にはサーバー側で一意な名前を採番してください。

| Config | 既定 | 対象 |
| --- | --- | --- |
| `MAX_CONTENT_LENGTH` | 8MiB | 入力body全体。nativeはconstructorの値を使う |
| `REQUEST_BODY_MEMORY_LIMIT` | 1MiB | 入力spool閾値。nativeはconstructorの`request_memory_limit` |
| `MAX_FORM_MEMORY_SIZE` | 1MiB | multipart非file field合計、URL-encoded form全体 |
| `MAX_FORM_PARTS` | 1000 | multipart part数 |
| `UPLOAD_MEMORY_LIMIT` | 1MiB | multipart fileごとのspool閾値 |

全値は正にしてください。part headerとpreambleにも64KiBの上限があります。
browserが送るbinary/8bit multipartを対象にし、Content-Transfer-Encodingのbase64等は拒否します。
過大入力は413、不完全・不正なmultipartは400です。

## リソースと運用境界

ファイルは通常応答、stream終了、クライアント切断、解析失敗で閉じます。
依存cleanupとbackground workが終わるまでは必要な入力資源を保持します。
request context外へUploadFileを保存して使い回さず、永続化が必要なら応答前に自分の保存先へコピーします。

標準の一時ディレクトリを使います。一時領域のquota・空き容量・権限・暗号化、
同時接続数・受信timeoutを運用側で設定してください。disk退避は容量制限の代わりではありません。
multipartは入力spoolとは別にfileをspoolするため、一時disk使用が重複することがあります。
ASGI/WSGIの上流サーバーやproxy自身のbufferingを遡って制限することもできません。

`examples/server_modes.py`の`/upload`でraw/multipartのSHA-256とサイズを確認できます。
実通信テストは3方式すべてで2MiB超の入力を検証します。
