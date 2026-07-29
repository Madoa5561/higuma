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
`auth.login_user(user, remember=True)`だけが永続cookieを発行します。
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
app.add_middleware(RateLimitMiddleware, limit=100, window=60, max_keys=10_000)
```

`max_keys`は大量の異なるclient identityによるメモリ増加を制限します。

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

## Secret key

session、token、OAuth stateに使うsecretはUTF-8で32バイト以上必須です。

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Reverse proxy

`X-Forwarded-For`と`X-Forwarded-Proto`は標準では無視されます。
直接接続元を限定して有効にします。

```python
app.add_middleware(
    ProxyHeadersMiddleware,
    trusted_proxies=("127.0.0.1", "10.0.0.0/8"),
)
```

## 原則

- passwordやtokenをログへ出さない
- secretをGitへcommitしない
- HTTPSとsecure cookieを使う
- credential付きCORSで`*`を使わない
- upload filenameをそのまま任意pathへ結合しない
- raw SQLへユーザー入力を文字列連結しない
