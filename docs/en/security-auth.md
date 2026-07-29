# Authentication and security

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

Use `auth.login_user(user)` to log in and `auth.logout_user()` to log out.
Only `auth.login_user(user, remember=True)` creates a persistent cookie.
`fresh_login_required`, `roles_required("admin")`, and
`permissions_required("posts:write")` are also available. User objects should
expose iterable `roles` or `permissions` attributes.

## Passwords

```python
hasher = PasswordHasher()
stored = hasher.hash(password)
valid = hasher.verify(password, stored)
```

The built-in password format uses scrypt with a random salt.

## CSRF and rate limits

```python
app.add_middleware(CSRFProtection)
app.add_middleware(RateLimitMiddleware, limit=100, window=60, max_keys=10_000)
```

Unsafe requests must send `_csrf_token` as a form field or `X-CSRF-Token`.
`max_keys` bounds memory use when many distinct client identities are observed.

## OAuth

```python
google = OAuth2Client.google(
    client_id=os.environ["GOOGLE_CLIENT_ID"],
    client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
    redirect_uri="https://example.com/auth/google/callback",
    secret_key=os.environ["HIGUMA_SECRET_KEY"],
)
```

`OAuth2Client.line(...)` and `OAuth2Client.discord(...)` use the same API.
Always call `validate_state()` before exchanging an authorization code.
With `SessionMiddleware`, state is bound to the browser session, accepted only
once, and PKCE S256 is added automatically.

## Secret keys

Secrets used for sessions, tokens, and OAuth state must contain at least 32
UTF-8 bytes.

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Reverse proxies

`X-Forwarded-For` and `X-Forwarded-Proto` are ignored by default. Enable them
only for known direct peers:

```python
app.add_middleware(
    ProxyHeadersMiddleware,
    trusted_proxies=("127.0.0.1", "10.0.0.0/8"),
)
```

## Production rules

- Never log passwords or tokens.
- Never commit secrets.
- Use HTTPS and secure cookies.
- Do not use wildcard credentialed CORS.
- Do not join an untrusted upload filename into an arbitrary path.
- Do not interpolate user input into raw SQL.
