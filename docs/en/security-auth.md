# Authentication and security

## Secret keys

Secrets used for sessions, signed tokens, and OAuth state must contain at least
32 UTF-8 bytes. Keep them out of repositories, images, logs, and exception
responses, and load production secrets from a secret manager.

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Signed cookie sessions

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

The cookie is signed, not encrypted. Do not store passwords, tokens, or
personal data in it. Modified, malformed, and expired cookies become empty
sessions. The serialized cookie is limited to 4093 bytes.

A browser session with `session.permanent = False` has no `Max-Age`; only a
`True` session creates a persistent cookie. Login and logout clear the session
to prevent fixation.

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

`AuthManager` also registers `SessionMiddleware`; do not stack another session
middleware on the same application. The loader receives a string ID on each
request and returns a user or `None`. A user exposes `id` and
`is_authenticated`.

- `login_user(user, remember=False)` rotates the session and marks login fresh
- `confirm_login()` marks existing login fresh; no login raises `Unauthorized`
- `logout_user()` clears the session
- `login_required` rejects anonymous requests with 401
- `fresh_login_required` rejects non-fresh login with 401
- `roles_required` / `permissions_required` reject missing claims with 403

Role and permission decorators read iterable `roles` and `permissions`
attributes. With `match_all=False`, any one claim is enough. An empty required
claim list raises `ValueError`.

## Passwords

```python
hasher = PasswordHasher()
stored = hasher.hash(password)

if hasher.verify(password, stored):
    if hasher.needs_rehash(stored):
        stored = hasher.hash(password)
```

The format uses scrypt and a random salt. `verify()` returns `False` for a
malformed or resource-limit-exceeding encoding. Store the encoded hash and
parameters; never store or log the plain password.

## Signed tokens

```python
signer = TokenSigner(os.environ["HIGUMA_SECRET_KEY"], salt="email-verify")
token = signer.dumps({"user_id": "usr_123"})
payload = signer.loads(token, max_age=900)
```

Use a different `salt` for each purpose. A token is signed, not encrypted.
Expiry, modification, or malformed input raises `ValueError`.

## CSRF

For a browser application with cookie sessions, add `CSRFProtection` inside
the session middleware.

```python
app.add_middleware(SessionMiddleware, os.environ["HIGUMA_SECRET_KEY"], secure=True)
app.add_middleware(CSRFProtection)


@app.get("/form")
def form():
    return {"csrf_token": csrf_token()}
```

POST, PUT, PATCH, and DELETE requests send `_csrf_token` as a form field or
`X-CSRF-Token` as a header. A mismatch returns 403. When a custom `field_name`
is configured, `csrf_token()` follows the active protection setting.

## Rate limits

```python
app.add_middleware(
    RateLimitMiddleware,
    limit=100,
    window=60,
    max_keys=10_000,
)
```

The default key is `request.remote_addr`. State is process-local and is not
shared between worker processes. Use a reverse proxy or shared store for a
strict global quota. Once `max_keys` is reached, unknown identities share an
overflow bucket instead of growing memory without bound.

## OAuth 2.0 with PKCE

OAuth flows require `SessionMiddleware`. State and the PKCE verifier are bound
to the browser session; multiple pending logins are retained and each state is
consumed once.

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

Call `validate_state()` before exchanging the code. The `google`, `line`, and
`discord` factories and the custom-provider constructor use the same flow.
Provider responses are limited to 1 MiB. An OIDC `nonce` is sent, but higuma is
not an ID-token signature or claim validator.

## CORS, hosts, and security headers

List exact origins for credentialed CORS; never use `*`.

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

Design CSP for the scripts, styles, images, and API origins the application
actually loads. Enable HSTS only when HTTPS is permanent for every covered
subdomain.

## Reverse proxies

`X-Forwarded-For` and `X-Forwarded-Proto` are ignored by default. Trust only a
direct proxy IP or CIDR, and make that proxy overwrite forwarded headers from
the public request.

```python
app.add_middleware(
    ProxyHeadersMiddleware,
    trusted_proxies=("127.0.0.1", "10.0.0.0/8"),
)
```

Never read `X-Forwarded-For` directly without this trust boundary. See
[Deployment](deployment.md) for the multi-process supervisor client-IP caveat.

## Checklist

Signing keys must be `str` or `bytes` containing at least 32 bytes; implicit
conversion from integers or arrays is rejected. Invalid non-ASCII CSRF tokens
return 403. `TrustedHostMiddleware` also rejects missing hosts, malformed ports,
and invalid IPv6 syntax. OAuth token and userinfo requests never follow redirects;
configure the provider's final HTTPS URL to avoid forwarding credentials to
another host or an HTTP endpoint.

- Never log passwords, sessions, OAuth tokens, or secrets
- Use HTTPS, secure/HTTP-only cookies, and an appropriate SameSite policy
- Use exact credentialed CORS and WebSocket origins
- Apply unique server-side names, size/type/quota checks, and scanning to uploads
- Never interpolate user input into raw SQL
- Verify `debug=False`, custom errors, and reverse-proxy limits in production
