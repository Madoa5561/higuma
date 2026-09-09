import sys

import pytest
from higuma import Higuma, Response, SessionMiddleware, request
from higuma.exceptions import BadRequest
from higuma.request import Request


def test_session_responses_vary_on_cookies_without_rewriting_unchanged_sessions():
    app = Higuma(__name__, static_folder=None)
    app.add_middleware(SessionMiddleware, "session-review-secret-at-least-32-bytes")

    @app.get("/profile")
    def profile():
        return Response(request.session.get("name", "anonymous"), headers={"vary": "Accept"})

    @app.post("/login")
    def login():
        request.session["name"] = "bear"
        return "ok"

    client = app.test_client()
    anonymous = client.get("/profile")
    assert anonymous.headers["vary"] == "Accept, Cookie"
    assert client.post("/login").headers["vary"] == "Cookie"
    profile_response = client.get("/profile")
    assert profile_response.text == "bear"
    assert profile_response.headers["vary"] == "Accept, Cookie"
    assert not any(name == "set-cookie" for name, _ in profile_response.header_items)


@pytest.mark.parametrize("vary", ["Origin, cOoKiE", "*"])
def test_session_preserves_existing_cookie_or_wildcard_vary(vary):
    app = Higuma(__name__, static_folder=None)
    app.add_middleware(SessionMiddleware, "session-review-secret-at-least-32-bytes")

    @app.get("/")
    def index():
        return Response("ok", headers={"vary": vary})

    assert app.test_client().get("/").headers["vary"] == vary


def test_json_parser_limits_are_client_errors_and_silent_parsing_is_safe():
    app = Higuma(__name__, static_folder=None)

    @app.post("/json")
    def parse():
        return {"value": request.get_json()}

    @app.post("/silent")
    def silent():
        return {"value": request.get_json(silent=True)}

    bodies = [b"[" * 2000 + b"0" + b"]" * 2000]
    if hasattr(sys, "get_int_max_str_digits") and sys.get_int_max_str_digits():
        bodies.append(b"9" * (sys.get_int_max_str_digits() + 1))
    client = app.test_client()
    for body in bodies:
        assert (
            client.post(
                "/json", data=body, headers={"content-type": "application/json"}
            ).status_code
            == 400
        )
        response = client.post("/silent", data=body, headers={"content-type": "application/json"})
        assert response.status_code == 200
        assert response.json == {"value": None}


def test_json_depth_boundary_and_failed_parse_cache():
    for depth in (128, 129):
        current = Request(
            {
                "headers": {"content-type": "application/json"},
                "body": b"[" * depth + b"0" + b"]" * depth,
            }
        )
        if depth == 128:
            assert isinstance(current.get_json(), list)
        else:
            assert current.get_json(silent=True) is None
            with pytest.raises(BadRequest):
                current.get_json()
