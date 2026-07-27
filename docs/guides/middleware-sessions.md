# MiddlewareとSession

## Hooks

```python
@app.before_request
def start_request():
    pass


@app.after_request
def add_header(response):
    response.headers["X-App"] = "higuma"
    return response
```

起動・終了時の処理にはlifecycle hookを使います。

```python
@app.before_serving
async def connect():
    await database.connect()


@app.after_serving
async def disconnect():
    await database.disconnect()
```

## Middleware

middlewareはrequestと次の処理を受け取ります。

```python
async def timing_middleware(request, call_next):
    response = await call_next(request)
    response.headers["Server-Timing"] = "app;dur=1"
    return response


app.add_middleware(timing_middleware)
```

## CORS

```python
from higuma import CORSMiddleware

app.add_middleware(
    CORSMiddleware(
        allow_origins=["https://example.com"],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
        allow_credentials=True,
    )
)
```

## セキュリティheaderとhost検証

```python
from higuma import SecurityHeadersMiddleware, TrustedHostMiddleware

app.add_middleware(SecurityHeadersMiddleware())
app.add_middleware(
    TrustedHostMiddleware(["example.com", "*.example.com"])
)
```

## 署名付きSession

```python
from higuma import SessionMiddleware

app.add_middleware(
    SessionMiddleware(
        secret_key="replace-with-a-long-random-secret",
        cookie_name="session",
        secure=True,
        httponly=True,
        samesite="Lax",
    )
)
```

```python
from higuma import request


@app.get("/login")
def login():
    request.session["user_id"] = 42
    return {"logged_in": True}
```

!!! danger "Secret key"
    source codeへ固定値を書かず、productionでは環境変数やsecret managerから読み込んでください。
    Cookieは署名されますが暗号化はされないため、機密データ自体を保存しないでください。
