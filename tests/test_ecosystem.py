from dataclasses import dataclass
from http.cookies import SimpleCookie
from unittest.mock import patch

import pytest
from higuma import AuthManager, Higuma, ProxyHeadersMiddleware, RateLimitMiddleware, Response


@pytest.mark.parametrize("name", ["higuma_session", "__Host-session", "__Secure-session"])
def test_logout_preserves_cookie_security_attributes(name):
    app = Higuma(__name__, static_folder=None)

    @dataclass
    class User:
        id: str = "user"
        is_authenticated: bool = True

    auth = AuthManager(
        app,
        secret_key="a-test-secret-with-at-least-32-bytes",
        user_loader=lambda _: User(),
        session_options={"cookie_name": name, "secure": True, "samesite": "Strict"},
    )

    @app.post("/login")
    def login():
        auth.login_user(User())
        return "ok"

    @app.post("/logout")
    def logout():
        auth.logout_user()
        return "ok"

    client = app.test_client()
    client.post("/login")
    response = client.post("/logout")
    cookie = SimpleCookie()
    cookie.load(next(value for key, value in response.header_items if key == "set-cookie"))
    assert cookie[name]["max-age"] == "0"
    assert cookie[name]["secure"]
    assert cookie[name]["httponly"]
    assert cookie[name]["samesite"] == "Strict"


def test_direct_prefixed_cookie_deletion_is_valid():
    response = Response()
    response.delete_cookie("__Host-session")
    assert "Secure" in response.header_items[0][1]


def test_rate_limit_churn_preserves_active_histories_and_bounds_overflow():
    app = Higuma(__name__, static_folder=None)
    with patch("higuma.security.time.monotonic", return_value=100):
        limiter = app.add_middleware(
            RateLimitMiddleware, limit=1, max_keys=2, key=lambda req: req.args.get("key")
        )

        @app.get("/")
        def index():
            return "ok"

        client = app.test_client()
        for key in ("a", "b", "c"):
            assert client.get(f"/?key={key}").status_code == 200
        assert client.get("/?key=a").status_code == 429
        assert client.get("/?key=d").status_code == 429
        assert len(limiter._requests) == 2
    with patch("higuma.security.time.monotonic", return_value=161):
        assert client.get("/?key=d").status_code == 200


def test_rate_limit_overflow_history_survives_dedicated_bucket_expiry():
    app = Higuma(__name__, static_folder=None)
    with patch("higuma.security.time.monotonic", return_value=100):
        app.add_middleware(
            RateLimitMiddleware, limit=1, max_keys=1, key=lambda req: req.args.get("key")
        )

        @app.get("/")
        def index():
            return "ok"

        client = app.test_client()
        assert client.get("/?key=a").status_code == 200
    with patch("higuma.security.time.monotonic", return_value=159):
        assert client.get("/?key=x").status_code == 200
    with patch("higuma.security.time.monotonic", return_value=160):
        assert client.get("/?key=x").status_code == 429
    with patch("higuma.security.time.monotonic", return_value=219):
        assert client.get("/?key=x").status_code == 200


@pytest.mark.parametrize(
    "forwarded,origin,status",
    [
        ("https", "http://localhost", 403),
        ("https", "https://localhost", 204),
        ("http", "http://localhost", 204),
        ("http", "https://localhost", 403),
    ],
)
def test_default_websocket_origin_uses_trusted_effective_scheme(forwarded, origin, status):
    app = Higuma(__name__, static_folder=None)
    app.add_middleware(ProxyHeadersMiddleware)

    @app.websocket("/ws")
    def websocket(ws):
        pass

    route = app._websocket_rules["websocket"]
    response = app._dispatch_websocket_preflight(
        route,
        {
            "headers": {"host": "localhost", "origin": origin, "x-forwarded-proto": forwarded},
            "client_addr": "127.0.0.1",
        },
    )
    assert response.status_code == status
