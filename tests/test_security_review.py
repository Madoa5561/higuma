from __future__ import annotations

import io
from email.message import Message
from unittest.mock import patch
from urllib.request import HTTPSHandler
from urllib.response import addinfourl

import pytest
from higuma import (
    CSRFProtection,
    OAuth2Client,
    SessionMiddleware,
    TokenSigner,
    TrustedHostMiddleware,
)
from higuma.exceptions import Forbidden
from higuma.request import Request
from higuma.sessions import Session

SECRET = "security-review-secret-at-least-32-bytes"


@pytest.mark.parametrize("factory", [TokenSigner, SessionMiddleware])
@pytest.mark.parametrize("invalid", [32, True, None, [0] * 32])
def test_signing_rejects_implicit_key_conversion(factory, invalid):
    with pytest.raises(TypeError):
        factory(invalid)


@pytest.mark.parametrize("secret", [SECRET, SECRET.encode()])
def test_valid_signing_keys_round_trip(secret):
    signer = TokenSigner(secret)
    assert signer.loads(signer.dumps({"user": 1})) == {"user": 1}


@pytest.mark.parametrize("supplied", ["é", "wrong", ""])
def test_csrf_invalid_tokens_are_forbidden(supplied):
    current = Request({"method": "POST", "headers": {"x-csrf-token": supplied}})
    current.session = Session({"_csrf_token": "valid-token"})
    with pytest.raises(Forbidden):
        CSRFProtection()(current, lambda _: pytest.fail("handler must not run"))


@pytest.mark.parametrize(
    "host",
    [
        "",
        "example.com:bad",
        "example.com:",
        "example.com:65536",
        "[::1]evil",
        "example.com:80@evil.test",
        "evil.test",
        "[bad]",
        "example.com/path",
    ],
)
def test_trusted_host_rejects_missing_malformed_and_untrusted_values(host):
    current = Request({"headers": {"host": host}})
    with pytest.raises(Forbidden):
        TrustedHostMiddleware(("example.com", "::1"))(
            current, lambda _: pytest.fail("handler must not run")
        )


@pytest.mark.parametrize("host", ["example.com", "EXAMPLE.COM:443", "[::1]:8000"])
def test_trusted_host_accepts_valid_authorities(host):
    current = Request({"headers": {"host": host}})
    assert TrustedHostMiddleware(("example.com", "::1"))(current, lambda _: "ok") == "ok"


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("target", ["https://other.test/steal", "http://provider.test/plain"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_oauth_never_follows_credential_bearing_redirects(status, target, method):
    calls = []

    def respond(_handler, outgoing):
        calls.append(outgoing)
        headers = Message()
        headers["Location"] = target
        response = addinfourl(io.BytesIO(b""), headers, outgoing.full_url, status)
        response.msg = "Redirect"
        return response

    with (
        patch.object(HTTPSHandler, "https_open", respond),
        pytest.raises(RuntimeError, match=f"HTTP {status}"),
    ):
        OAuth2Client._request_json(
            "https://provider.test/token",
            method=method,
            data={"client_secret": "test-secret"} if method == "POST" else None,
            headers={"authorization": "Bearer test-token"},
        )
    assert len(calls) == 1
    assert calls[0].full_url == "https://provider.test/token"


def test_oauth_still_accepts_successful_json():
    response = addinfourl(io.BytesIO(b'{"id":"user"}'), Message(), "https://provider.test", 200)
    response.msg = "OK"
    with patch.object(HTTPSHandler, "https_open", return_value=response):
        assert OAuth2Client._request_json("https://provider.test") == {"id": "user"}


def test_duplicate_request_headers_merge_per_rfc9110_across_middlewares():
    from higuma import CORSMiddleware, ProxyHeadersMiddleware

    current = Request(
        {
            "method": "OPTIONS",
            "client_addr": "127.0.0.1",
            "raw_headers": [
                (b"x-forwarded-for", b"203.0.113.10"),
                (b"x-forwarded-for", b"10.0.0.2"),
                (b"origin", b"https://allowed.example"),
                (b"access-control-request-method", b"POST"),
                (b"access-control-request-headers", b"x-forbidden"),
                (b"access-control-request-headers", b"x-allowed"),
                (b"accept", b"application/json"),
                (b"accept", b"text/html"),
            ],
        }
    )
    assert current.headers["x-forwarded-for"] == "203.0.113.10, 10.0.0.2"
    assert current.headers["accept"] == "application/json, text/html"

    ProxyHeadersMiddleware(("127.0.0.1", "10.0.0.0/8"))(current, lambda _: "ok")
    assert current.client_addr == "203.0.113.10"

    cors = CORSMiddleware(
        allow_origins=("https://allowed.example",),
        allow_headers=("x-allowed",),
    )
    with pytest.raises(Forbidden, match="CORS headers"):
        cors(current, lambda _: pytest.fail("handler must not run"))

    duplicate_host = Request(
        {
            "raw_headers": [
                (b"host", b"evil.test"),
                (b"host", b"example.com"),
            ]
        }
    )
    with pytest.raises(Forbidden, match="invalid Host"):
        TrustedHostMiddleware(("example.com",))(
            duplicate_host, lambda _: pytest.fail("handler must not run")
        )


def test_auth_login_rotates_custom_csrf_field_and_string_claims_do_not_split():
    from dataclasses import dataclass

    from higuma import AuthManager, Higuma, csrf_token
    from higuma.auth import _run_auth_checks

    @dataclass
    class User:
        id: str
        roles: str
        permissions: str

    app = Higuma(__name__, static_folder=None)
    auth = AuthManager(app, secret_key=SECRET, user_loader=lambda uid: User(uid, "admin", "write"))
    app.add_middleware(CSRFProtection(field_name="custom_csrf"))

    @app.get("/token")
    def get_token():
        return {"token": csrf_token()}

    @app.post("/login")
    def do_login():
        auth.login_user(User("u1", "admin", "write"))
        return {"token": csrf_token()}

    @app.get("/role-char")
    @auth.roles_required("a")
    def role_char():
        return "forbidden"

    @app.get("/role-admin")
    @auth.roles_required("admin")
    def role_admin():
        return "allowed"

    @app.websocket("/ws-role")
    @auth.roles_required("a")
    def ws_role(ws):
        pass

    client = app.test_client()
    initial = client.get("/token").json["token"]
    login_resp = client.post("/login", headers={"x-csrf-token": initial})
    assert login_resp.status_code == 200
    rotated = login_resp.json["token"]
    assert rotated != initial
    assert client.get("/role-char").status_code == 403
    assert client.get("/role-admin").status_code == 200

    # Direct preflight auth check must also not split string claims into characters.
    with app.test_request_context("/ws-role"):
        from higuma.auth import _current_user

        token = _current_user.set(User("u1", "admin", "write"))
        try:
            with pytest.raises(Forbidden):
                _run_auth_checks(app._websocket_rules["ws_role"].view_func)
        finally:
            _current_user.reset(token)


def test_oauth2_authorization_url_existing_query_and_non_ascii_state():
    from higuma import Higuma, session

    app = Higuma(__name__, static_folder=None)
    app.add_middleware(SessionMiddleware, SECRET)
    oauth = OAuth2Client(
        client_id="cid",
        client_secret="csec",
        authorize_url="https://provider.test/auth?tenant=acme",
        token_url="https://provider.test/token",
        userinfo_url="https://provider.test/userinfo",
        redirect_uri="https://app.test/callback",
        scopes=("openid",),
        secret_key=SECRET,
    )

    @app.get("/start")
    def start():
        url = oauth.authorization_url()
        # Inject a non-ASCII pending key alongside the valid nonce.
        session[oauth._oauth_session_key]["日本語-nonce"] = {"verifier": "ignored"}
        return {"url": url}

    @app.get("/callback")
    def callback():
        from higuma import request

        oauth.validate_state(request.args["state"])
        return {"ok": True}

    client = app.test_client()
    url = client.get("/start").json["url"]
    assert url.startswith("https://provider.test/auth?tenant=acme&")
    assert url.count("?") == 1
    from urllib.parse import parse_qs, urlsplit

    state = parse_qs(urlsplit(url).query)["state"][0]
    assert client.get("/callback", query_string={"state": state}).json == {"ok": True}


def test_password_hasher_supports_owasp_n_and_caps_scrypt_memory():
    from higuma import PasswordHasher

    hasher = PasswordHasher(n=2**15, r=8, p=1)
    encoded = hasher.hash("correct horse battery staple")
    assert hasher.verify("correct horse battery staple", encoded)
    assert not hasher.verify("wrong password", encoded)

    with pytest.raises(ValueError, match="memory limit"):
        PasswordHasher(n=2**19, r=8, p=1)

    crafted = "higuma$scrypt$1048576$32$1$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    assert not hasher.verify("any", crafted)


def test_supervisor_bind_target_supports_ipv4_and_ipv6():
    import socket

    from higuma.supervisor import _bind_target

    assert _bind_target("127.0.0.1") == ("127.0.0.1", socket.AF_INET)
    assert _bind_target("::1") == ("::1", socket.AF_INET6)
    assert _bind_target("[::1]") == ("::1", socket.AF_INET6)
    assert _bind_target("::") == ("::", socket.AF_INET6)


def test_middleware_rejects_bare_string_iterables_and_security_parameter_bounds():
    from higuma import (
        CORSMiddleware,
        ProxyHeadersMiddleware,
        RateLimitMiddleware,
        generate_user_id,
        validate_user_id,
    )

    with pytest.raises(TypeError, match="iterable of strings"):
        CORSMiddleware(allow_origins="https://example.com")
    with pytest.raises(ValueError, match="max_age"):
        CORSMiddleware(max_age=-1)
    with pytest.raises(ValueError, match="max_age"):
        CORSMiddleware(max_age=True)
    with pytest.raises(TypeError, match="iterable of strings"):
        TrustedHostMiddleware("example.com")
    with pytest.raises(TypeError, match="iterable of strings"):
        ProxyHeadersMiddleware("127.0.0.1")

    for bad_kwargs in (
        {"limit": True},
        {"window": True},
        {"window": float("nan")},
        {"window": float("inf")},
        {"max_keys": True},
    ):
        with pytest.raises(ValueError):
            RateLimitMiddleware(**bad_kwargs)

    signer = TokenSigner(SECRET)
    token = signer.dumps({"ok": True})
    for bad_age in (True, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="max_age"):
            signer.loads(token, max_age=bad_age)

    assert validate_user_id(generate_user_id("usr_1", entropy_bytes=12))
    for bad_call in (
        lambda: generate_user_id("_bad"),
        lambda: generate_user_id("熊"),
        lambda: generate_user_id("usr", entropy_bytes=2),
        lambda: generate_user_id("usr", entropy_bytes=True),
        lambda: generate_user_id("usr", entropy_bytes=120),
    ):
        with pytest.raises(ValueError):
            bad_call()


def test_websocket_preflight_rejects_empty_host_and_port_zero_and_matches_ipv6():
    from higuma import Higuma

    app = Higuma(__name__, static_folder=None)

    @app.websocket("/ws")
    def ws_handler(ws):
        return None

    route = app._websocket_rules["ws_handler"]

    def preflight(host: str, origin: str) -> int:
        raw = {
            "method": "GET",
            "path": "/ws",
            "scheme": "http",
            "raw_headers": [
                (b"host", host.encode("latin-1")),
                (b"origin", origin.encode("latin-1")),
            ],
        }
        return app._dispatch_websocket_preflight(route, raw).status_code

    assert preflight("", "http://") == 403
    assert preflight("example.com", "http://example.com:0") == 403
    assert preflight("example.com:0", "http://example.com:0") == 403
    assert preflight("good.com/evil", "http://good.com") == 403
    assert preflight("[0:0:0:0:0:0:0:1]:8000", "http://[::1]:8000") == 204


def test_login_logout_syncs_current_user_and_oauth_session_validation(monkeypatch):
    from dataclasses import dataclass

    from higuma import AuthManager, Higuma, OAuth2Client, SessionMiddleware, current_user, request
    from higuma.redis_state import RedisSessionMiddleware, RedisSessionStore

    @dataclass
    class User:
        id: str
        is_authenticated: bool = True

    app = Higuma(__name__, static_folder=None)
    auth = AuthManager(app, secret_key=SECRET)
    users = {"u1": User("u1")}
    auth.load_user(users.get)

    @app.post("/login")
    def login():
        auth.login_user(users["u1"])
        return {
            "id": current_user.id,
            "auth": current_user.is_authenticated,
            "req_user": request.user.id,
        }

    @app.post("/logout")
    def logout():
        auth.logout_user()
        return {
            "id": current_user.id,
            "auth": current_user.is_authenticated,
            "req_auth": request.user.is_authenticated,
        }

    client = app.test_client()
    assert client.post("/login").json == {"id": "u1", "auth": True, "req_user": "u1"}
    assert client.post("/logout").json == {"id": None, "auth": False, "req_auth": False}

    # OAuth2Client scopes validation, custom preset scopes, access_token check, and GET content-type.
    with pytest.raises(TypeError, match="scopes"):
        OAuth2Client.google(
            client_id="cid",
            client_secret="csec",
            redirect_uri="https://app.test/cb",
            secret_key=SECRET,
            scopes="openid email",  # type: ignore[arg-type]
        )
    custom = OAuth2Client.google(
        client_id="cid",
        client_secret="csec",
        redirect_uri="https://app.test/cb",
        secret_key=SECRET,
        scopes=("openid", "email"),
    )
    assert custom.scopes == ("openid", "email")
    with pytest.raises(ValueError, match="access_token"):
        custom.userinfo("bad\r\nX-Injected: 1")

    seen_headers: dict[str, str] = {}

    class FakeOpener:
        def open(self, req, timeout=10):
            seen_headers.update({k.lower(): v for k, v in req.header_items()})

            class Resp:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def read(self, size=-1):
                    return b'{"sub":"u1"}'

            return Resp()

    monkeypatch.setattr("higuma.auth.build_opener", lambda *_: FakeOpener())
    assert custom.userinfo("valid-token") == {"sub": "u1"}
    assert "content-type" not in seen_headers

    for bad_session in (
        {"cookie_name": ""},
        {"cookie_name": "bad;name"},
        {"max_age": 0},
        {"max_age": True},
        {"samesite": "Invalid"},
    ):
        with pytest.raises(ValueError):
            SessionMiddleware(SECRET, **bad_session)  # type: ignore[arg-type]

    store = RedisSessionStore(object(), prefix="app:", ttl=1)
    with pytest.raises(ValueError, match="max_age"):
        RedisSessionMiddleware(SECRET, store=store, max_age=True)  # type: ignore[arg-type]


def test_auth_proxy_eq_async_decorators_session_checks_and_oauth_endpoint_validation():
    import asyncio
    import inspect
    from dataclasses import dataclass

    from higuma import (
        AuthManager,
        Higuma,
        OAuth2Client,
        SessionMiddleware,
        current_user,
        fresh_login_required,
        login_required,
        permissions_required,
        roles_required,
    )

    @dataclass(frozen=True)
    class User:
        id: str
        roles: tuple[str, ...] = ("admin",)
        permissions: tuple[str, ...] = ("write",)
        is_authenticated: bool = True

    with pytest.raises(ValueError, match="session_key"):
        AuthManager(session_key="")
    with pytest.raises(ValueError, match="non-empty"):
        roles_required("")
    with pytest.raises(ValueError, match="non-empty"):
        permissions_required(123)  # type: ignore[arg-type]

    # AuthManager without SessionMiddleware raises clear RuntimeError
    bare_app = Higuma(__name__, static_folder=None)
    bare_auth = AuthManager(secret_key=SECRET)
    bare_app.add_middleware(bare_auth)

    @bare_app.get("/bare")
    def bare_view():
        return "ok"

    with pytest.raises(RuntimeError, match="SessionMiddleware"):
        bare_auth(Request({"path": "/bare"}), lambda req: "ok")

    # _CurrentUserProxy __eq__/__hash__ and async auth decorator preservation
    alice = User("alice")
    app = Higuma(__name__, static_folder=None)
    auth = AuthManager(
        app, secret_key=SECRET, user_loader=lambda uid: alice if uid == "alice" else None
    )

    @app.post("/login")
    def do_login():
        from higuma.auth import _CurrentUserProxy

        auth.login_user(alice)
        return {
            "eq": current_user == alice,
            "self_eq": current_user == _CurrentUserProxy(),
            "hash": hash(current_user) == hash(alice),
        }

    @login_required
    async def secret_handler():
        await asyncio.sleep(0)
        return {"user": current_user.id, "eq": current_user == alice}

    @fresh_login_required
    @roles_required("admin")
    @permissions_required("write")
    async def admin_handler():
        await asyncio.sleep(0)
        return {"ok": True}

    assert inspect.iscoroutinefunction(secret_handler)
    assert inspect.iscoroutinefunction(admin_handler)
    app.get("/secret")(secret_handler)
    app.get("/admin")(admin_handler)

    client = app.test_client()
    assert client.get("/secret").status_code == 401
    assert client.post("/login").json == {"eq": True, "self_eq": True, "hash": True}
    assert client.get("/secret").json == {"user": "alice", "eq": True}
    assert client.get("/admin").json == {"ok": True}

    # OAuth2Client endpoint/credential/code validation
    with pytest.raises(ValueError, match="authorize_url"):
        OAuth2Client(
            client_id="cid",
            client_secret="csec",
            authorize_url="javascript:alert(1)",
            token_url="https://provider.test/token",
            userinfo_url="https://provider.test/userinfo",
            redirect_uri="https://app.test/cb",
            scopes=("openid",),
            secret_key=SECRET,
        )
    with pytest.raises(ValueError, match="redirect_uri"):
        OAuth2Client.google(
            client_id="cid",
            client_secret="csec",
            redirect_uri="javascript:alert(1)",
            secret_key=SECRET,
        )
    with pytest.raises(ValueError, match="client_id"):
        OAuth2Client.google(
            client_id="  ",
            client_secret="csec",
            redirect_uri="https://app.test/cb",
            secret_key=SECRET,
        )

    oauth = OAuth2Client.google(
        client_id="cid",
        client_secret="csec",
        redirect_uri="https://app.test/cb",
        secret_key=SECRET,
    )
    oauth_app = Higuma(__name__, static_folder=None)
    oauth_app.add_middleware(SessionMiddleware, SECRET)

    @oauth_app.get("/start")
    def start():
        from urllib.parse import parse_qs, urlsplit

        url = oauth.authorization_url()
        return {"state": parse_qs(urlsplit(url).query)["state"][0]}

    @oauth_app.get("/cb")
    def cb():
        from higuma import request as active_request

        oauth.validate_state(active_request.args["state"])
        with pytest.raises(ValueError, match="authorization code"):
            oauth.fetch_token("")
        # Verifier must still be present (not consumed by the rejected empty code)
        return {"has_verifier": oauth._oauth_verifier_key in active_request.state}

    oauth_client = oauth_app.test_client()
    state = oauth_client.get("/start").json["state"]
    assert oauth_client.get(f"/cb?state={state}").json == {"has_verifier": True}
