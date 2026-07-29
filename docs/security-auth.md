# 認証とセキュリティ

## Session login

```python
auth = AuthManager(app, secret_key=os.environ["HIGUMA_SECRET_KEY"])


@auth.load_user
def load_user(user_id):
    return find_user(user_id)


@app.get("/me")
@auth.login_required
def me():
    return {"id": current_user.id}
```

login時は`auth.login_user(user)`、logout時は`auth.logout_user()`を使います。
`fresh_login_required`、`roles_required("admin")`、
`permissions_required("posts:write")`も利用できます。User objectは
`roles`または`permissions` iterableを持たせてください。

## Password

```python
hasher = PasswordHasher()
stored = hasher.hash(password)
valid = hasher.verify(password, stored)
```

標準実装はランダムsalt付き`scrypt`です。

## CSRFとrate limit

```python
app.add_middleware(CSRFProtection)
app.add_middleware(RateLimitMiddleware, limit=100, window=60)
```

unsafe methodではformの`_csrf_token`または`X-CSRF-Token`を送信します。

## OAuth

```python
google = OAuth2Client.google(
    client_id=os.environ["GOOGLE_CLIENT_ID"],
    client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
    redirect_uri="https://example.com/auth/google/callback",
    secret_key=os.environ["HIGUMA_SECRET_KEY"],
)
```

`OAuth2Client.line(...)`と`OAuth2Client.discord(...)`も同じAPIです。
callbackでは必ず`validate_state()`を先に実行してください。
`SessionMiddleware`が有効ならstateはブラウザsessionに束縛されて単回使用になり、
PKCE S256も自動的に追加されます。

## 原則

- passwordやtokenをログへ出さない
- secretをGitへcommitしない
- HTTPSとsecure cookieを使う
- credential付きCORSで`*`を使わない
- upload filenameをそのまま任意pathへ結合しない
- raw SQLへユーザー入力を文字列連結しない
