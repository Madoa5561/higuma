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
