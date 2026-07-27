# RequestとResponse

## Request

引数として受け取る方法と、context-localな `request` proxyを利用する方法があります。

```python
from higuma import request


@app.post("/inspect")
def inspect():
    return {
        "method": request.method,
        "path": request.path,
        "query": request.query.get("q"),
        "agent": request.headers.get("user-agent"),
        "json": request.json,
    }
```

### よく使う属性

| 属性 | 内容 |
|---|---|
| `method` | HTTP method |
| `path` | URL path |
| `query` / `args` | query parameterの `MultiDict` |
| `headers` | 大文字小文字を区別しないheaders |
| `cookies` | cookie辞書 |
| `body` | raw bytes |
| `text` | decode済みbody |
| `json` | JSON body |
| `form` | URL encoded form |
| `path_params` | 変換済みroute parameter |

同名query parameterは `getlist()` で取得します。

```python
tags = request.query.getlist("tag")
```

## Response

handlerは文字列、bytes、辞書、list、`Response`、tupleを返せます。

```python
return "OK"
return {"status": "ok"}
return {"created": True}, 201
return "Accepted", 202, {"X-Job-ID": "42"}
```

### 明示的なresponse

```python
from higuma import HTMLResponse, JSONResponse, redirect


@app.get("/html")
def html():
    return HTMLResponse("<strong>Hello</strong>")


@app.get("/api")
def api():
    return JSONResponse({"ok": True}, status=200)


@app.get("/old")
def old():
    return redirect("/new", status=308)
```

### Cookie

```python
response = app.make_response("saved")
response.set_cookie(
    "theme",
    "forest",
    max_age=86400,
    httponly=True,
    secure=True,
    samesite="Lax",
)
return response
```

### ファイル送信

```python
from higuma import send_file


@app.get("/report")
def report():
    return send_file(
        "reports/latest.pdf",
        as_attachment=True,
        download_name="report.pdf",
    )
```

FileResponseはETag、conditional GET、Range requestに対応します。

## 非同期handler

```python
@app.get("/async")
async def async_view():
    result = await fetch_something()
    return {"result": result}
```

同期・非同期handlerを同じrouting APIで登録できます。
