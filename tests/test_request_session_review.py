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


def test_request_cookies_survive_bracketed_reserved_and_malformed_siblings():
    current = Request(
        {
            "headers": {
                "cookie": (
                    "filter[page]=1; path=/admin; malformed_token; "
                    'higuma_session=valid_token; quoted="a; b"'
                )
            }
        }
    )
    assert dict(current.cookies) == {
        "filter[page]": "1",
        "path": "/admin",
        "higuma_session": "valid_token",
        "quoted": "a; b",
    }


def test_mounted_asgi_app_with_async_handler_and_unicode_prefix():
    import asyncio

    sub = Higuma(__name__, static_folder=None)

    @sub.get("/items")
    async def items():
        await asyncio.sleep(0.01)
        return {"mounted": True, "root": request.root_path, "path": request.path}

    app = Higuma(__name__, static_folder=None)
    app.mount_asgi("/管理", sub.asgi_app)

    response = app.test_client().get("/管理/items")
    assert response.status_code == 200
    assert response.get_json() == {"mounted": True, "root": "/管理", "path": "/items"}


def test_blueprint_relative_rules_zero_arg_teardown_and_optional_hook_defaults():
    from higuma import Blueprint

    app = Higuma(__name__, static_folder=None)
    events = []

    parent = Blueprint("api", __name__, url_prefix="api")
    child = Blueprint("v1", __name__, url_prefix="v1")

    @child.get("items")
    def list_items():
        return {"items": []}

    parent.register_blueprint(child)
    app.register_blueprint(parent)

    @app.after_request
    def add_tag(response, tag="v1"):
        response.headers["x-tag"] = tag
        return response

    @app.teardown_request
    def teardown_req():
        events.append("teardown_request")

    @app.teardown_appcontext
    def teardown_app():
        events.append("teardown_appcontext")

    response = app.test_client().get("/api/v1/items")
    assert response.status_code == 200
    assert response.get_json() == {"items": []}
    assert response.headers["x-tag"] == "v1"
    assert events == ["teardown_request", "teardown_appcontext"]
