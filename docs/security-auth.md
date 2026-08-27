# 認証とセキュリティ

## Secret key

session、signed token、OAuth stateに使うsecretはUTF-8で32バイト以上必須です。
repository、image、log、exception responseへ含めず、productionではsecret managerから
読み込みます。

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Signed cookie session

```python
from higuma import SessionMiddleware

app.add_middleware(
    SessionMiddleware,
    os.environ["HIGUMA_SECRET_KEY"],
    cookie_name="higuma_session",
    max_age=14 * 24 * 60 * 60,
    secure=True,
    httponly=True,
    samesite="Lax",
)
```

cookieは署名されますが暗号化されません。password、token、個人情報をsessionへ保存しないで
ください。改変、不正形式、期限切れcookieは空sessionとして扱われます。serialized cookieは
4093 bytesまでです。

`session.permanent = False`のbrowser sessionは`Max-Age`を付けず、`True`のsessionだけ
persistent cookieになります。login/logout時はsessionをclearして固定化攻撃を防ぎます。

## Session login

```python
auth = AuthManager(
    app,
    secret_key=os.environ["HIGUMA_SECRET_KEY"],
    session_options={"secure": True, "samesite": "Lax"},
)


@auth.load_user
def load_user(user_id):
    return find_user(user_id)


@app.get("/me")
@auth.login_required
def me():
    return {"id": current_user.id}
```

`AuthManager`は`SessionMiddleware`も登録します。同じappへ別の`SessionMiddleware`を
重ねないでください。user loaderはrequestごとに文字列IDを受け、userまたは`None`を返します。
user objectは`id`と`is_authenticated`を持たせます。

- `login_user(user, remember=False)`: sessionをrotateしてfresh loginにする
- `confirm_login()`: 既存loginをfreshに戻す。未loginなら`Unauthorized`
- `logout_user()`: sessionをclearする
- `login_required`: 未loginを401にする
- `fresh_login_required`: non-fresh loginを401にする
- `roles_required` / `permissions_required`: claim不足を403にする

role / permission decoratorはuser objectの`roles` / `permissions` iterableを読みます。
`match_all=False`ならいずれか一つで許可します。空のrequired claimは`ValueError`です。

## Password

```python
hasher = PasswordHasher()
stored = hasher.hash(password)

if hasher.verify(password, stored):
    if hasher.needs_rehash(stored):
        stored = hasher.hash(password)
```

標準形式はrandom salt付き`scrypt`です。`verify()`は不正formatやresource上限外の値に対して
`False`を返します。password hashとparameterはdatabaseへ保存し、plain passwordは保存・log
しないでください。

## Signed token

```python
signer = TokenSigner(os.environ["HIGUMA_SECRET_KEY"], salt="email-verify")
token = signer.dumps({"user_id": "usr_123"})
payload = signer.loads(token, max_age=900)
```

用途ごとに異なる`salt`を使います。tokenは署名されますが暗号化されません。期限切れ、改変、
不正formatは`ValueError`です。

## CSRF

cookie sessionを使うbrowser applicationでは、session middlewareの内側に
`CSRFProtection`を追加します。

```python
app.add_middleware(SessionMiddleware, os.environ["HIGUMA_SECRET_KEY"], secure=True)
app.add_middleware(CSRFProtection)


@app.get("/form")
def form():
    return {"csrf_token": csrf_token()}
```

POST / PUT / PATCH / DELETEではform field `_csrf_token`、またはheader
`X-CSRF-Token`を送信します。token不一致は403です。custom `field_name`を設定した場合、
`csrf_token()`はactive protectionのfield設定へ追従します。

## Rate limit

```python
app.add_middleware(
    RateLimitMiddleware,
    limit=100,
    window=60,
    max_keys=10_000,
)
```

default keyは`request.remote_addr`です。状態はprocess-local memoryにあり、複数process間で
共有されません。厳密なglobal quotaにはreverse proxyやshared storeを使います。
`max_keys`到達後の未知keyは共通bucketへ集約され、無制限にmemoryを増やしません。

## OAuth 2.0 + PKCE

OAuth flowには`SessionMiddleware`が必須です。stateとPKCE verifierをbrowser sessionへ
束縛し、複数のpending loginを保持しながら各stateを一度だけ消費します。

```python
app.add_middleware(
    SessionMiddleware,
    os.environ["HIGUMA_SECRET_KEY"],
    secure=True,
)

google = OAuth2Client.google(
    client_id=os.environ["GOOGLE_CLIENT_ID"],
    client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
    redirect_uri="https://example.com/auth/google/callback",
    secret_key=os.environ["HIGUMA_SECRET_KEY"],
)


@app.get("/login/google")
def login_google():
    return redirect(google.authorization_url())


@app.get("/auth/google/callback")
def google_callback():
    google.validate_state(request.args["state"])
    token = google.fetch_token(request.args["code"])
    return google.userinfo(token["access_token"])
```

callbackではtoken交換前に`validate_state()`を呼びます。`google`、`line`、`discord` factoryと
custom provider constructorが同じflowを使います。provider responseは1 MiBまでです。
OIDC `nonce`は送信しますが、higumaはID token signature / claim validatorではありません。

## CORS、Host、security header

credential付きCORSはexact originを列挙し、`*`を使いません。

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=("https://app.example.com",),
    allow_credentials=True,
)
app.add_middleware(TrustedHostMiddleware, ("example.com", "*.example.com"))
app.add_middleware(
    SecurityHeadersMiddleware,
    strict_transport_security="max-age=31536000; includeSubDomains",
)
```

CSPはapplicationが実際に読み込むscript、style、image、API originへ合わせて設計してください。
HSTSはHTTPSが全subdomainで継続利用できる場合だけ有効にします。

## Reverse proxy

`X-Forwarded-For`と`X-Forwarded-Proto`は標準では無視されます。直接接続元proxyだけを
CIDRまたはIPで信頼します。proxyは外部から届いたforwarded headerを必ず上書きします。

```python
app.add_middleware(
    ProxyHeadersMiddleware,
    trusted_proxies=("127.0.0.1", "10.0.0.0/8"),
)
```

`ProxyHeadersMiddleware`を設定せず`X-Forwarded-For`を直接読むことは禁止です。
multi-process supervisorのclient IP制約は[デプロイ](deployment.md)を参照してください。

## Checklist

- password、session、OAuth token、secretをlogしない
- HTTPS、secure / httponly cookie、適切なSameSiteを使う
- credential付きCORSとWebSocket Originをexact matchにする
- uploadに一意なserver-side name、size/type/quota/scanを適用する
- raw SQLへuser inputを文字列連結しない
- `debug=False`、custom error response、reverse proxy limitを本番で確認する
