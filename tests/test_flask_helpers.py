import importlib

import pytest
from higuma import Higuma, Request, SessionMiddleware, after_this_request
from higuma import request as higuma_request


@pytest.fixture(params=["higuma", "flask"])
def framework(request):
    module = pytest.importorskip(request.param)
    app = getattr(module, "Higuma", getattr(module, "Flask", None))(__name__, static_folder=None)
    return module, app


def test_request_context_matches_flask_and_does_not_dispatch(framework):
    module, app = framework
    seen = []

    @app.before_request
    def before():
        seen.append("before")

    @app.get("/items/<int:number>")
    def item(number):
        seen.append("view")
        return {"number": number}

    @app.teardown_request
    def teardown(error):
        seen.append(("teardown", error, module.request.endpoint, module.has_request_context()))

    with app.test_request_context(
        "/items/7", query_string=[("tag", "a"), ("tag", "b")], base_url="https://example.test"
    ):
        assert module.has_request_context() and module.has_app_context()
        assert module.request.endpoint == "item"
        assert module.request.view_args == {"number": 7}
        assert module.request.args.getlist("tag") == ["a", "b"]
        assert module.request.host_url == "https://example.test/"
        assert module.request.url == "https://example.test/items/7?tag=a&tag=b"
        assert module.url_for("item", number=3) == "/items/3"
        assert seen == []
    assert seen == [("teardown", None, "item", True)]
    assert not module.has_request_context() and not module.has_app_context()


def test_nested_request_contexts_restore_globals_and_capture_errors(framework):
    module, app = framework
    seen = []
    app.teardown_request(lambda error: seen.append((module.request.path, error)))
    error = ValueError("context test")
    with app.app_context():
        module.g.owner = "outer"
        with app.test_request_context("/outer"):
            with pytest.raises(ValueError), app.test_request_context("/inner"):
                assert module.g.owner == "outer"
                raise error
            assert module.request.path == "/outer"
        assert not module.has_request_context() and module.g.owner == "outer"
    assert seen == [("/inner", error), ("/outer", None)]


def test_request_callback_and_teardown_order_matches_flask(framework):
    module, app = framework
    seen = []

    @app.after_request
    def after_first(response):
        seen.append("global-first")
        return response

    @app.after_request
    def after_second(response):
        seen.append("global-second")
        return response

    app.teardown_request(lambda error: seen.append(("teardown-first", error)))
    app.teardown_request(lambda error: seen.append(("teardown-second", error)))

    @app.get("/")
    def index():
        @module.after_this_request
        def local_first(response):
            seen.append("local-first")
            response.headers["X-Local"] = "ok"
            return response

        @module.after_this_request
        def local_second(response):
            seen.append("local-second")
            return response

        return "ok"

    response = app.test_client().get("/")
    assert response.headers["X-Local"] == "ok"
    assert seen == [
        "local-first",
        "local-second",
        "global-second",
        "global-first",
        ("teardown-second", None),
        ("teardown-first", None),
    ]
    with pytest.raises(RuntimeError):
        module.after_this_request(lambda response: response)


def test_post_response_session_changes_are_saved(framework):
    module, app = framework
    if module.__name__ == "flask":
        app.secret_key = "flask-helper-test-secret-with-at-least-32-bytes"
    else:
        app.add_middleware(SessionMiddleware, "flask-helper-test-secret-with-at-least-32-bytes")

    @app.after_request
    def count(response):
        module.session["global"] = module.session.get("global", 0) + 1
        return response

    @app.get("/write")
    def write():
        @module.after_this_request
        def local(response):
            module.session["local"] = "saved"
            return response

        return "ok"

    @app.get("/read")
    def read():
        return dict(module.session)

    client = app.test_client()
    assert client.get("/write").status_code == 200
    assert client.get("/read").get_json() == {"local": "saved", "global": 1}


@pytest.mark.parametrize("body", [b'"\xff"', b'{"x":'])
def test_bad_json_bytes_are_not_silently_replaced(framework, body):
    module, app = framework

    @app.post("/")
    def index():
        assert module.request.get_json(silent=True) is None
        return {"data": module.request.get_json()}

    assert (
        app.test_client().post("/", data=body, content_type="application/json").status_code == 400
    )


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-32"])
def test_json_encodings_match_flask(framework, encoding):
    module, app = framework

    @app.post("/")
    def index():
        return {"data": module.request.get_json()}

    response = app.test_client().post(
        "/", data='{"bear":"日本"}'.encode(encoding), content_type="application/problem+json"
    )
    assert response.get_json() == {"data": {"bear": "日本"}}


def test_values_query_priority_and_get_form_exclusion(framework):
    module, app = framework

    @app.route("/", methods=["GET", "POST"])
    def index():
        return {
            "selected": module.request.values["name"],
            "all": module.request.values.getlist("name"),
            "dict": module.request.values.to_dict(),
        }

    client = app.test_client()
    for method, expected in (("GET", ["query"]), ("POST", ["query", "form"])):
        response = client.open("/?name=query", method=method, data={"name": "form"})
        assert response.get_json() == {
            "selected": "query",
            "all": expected,
            "dict": {"name": "query"},
        }


def test_client_json_null_and_https_origin(framework):
    module, app = framework

    @app.post("/")
    def index():
        return {"data": module.request.get_json(), "scheme": module.request.scheme}

    response = app.test_client().post(
        "/", data="null", content_type="application/json", base_url="https://example.test"
    )
    assert response.get_json() == {"data": None, "scheme": "https"}


def test_response_helpers_match_flask(framework):
    module, _ = framework
    response = module.Response('{"a":1}', headers={"Content-Type": "text/plain"})
    assert response.get_json() is None
    assert response.get_json(force=True) == {"a": 1}
    assert response.get_data() == b'{"a":1}'
    assert response.get_data(as_text=True) == '{"a":1}'
    response.data = "updated"
    assert response.data == b"updated"
    assert response.get_json(force=True, silent=True) is None


def test_async_request_callback_and_context_cleanup():
    module = importlib.import_module("higuma")
    app = Higuma(__name__, static_folder=None)
    seen = []

    @app.teardown_request
    async def teardown(error):
        seen.append((higuma_request.path, error))

    @app.get("/")
    def index():
        @after_this_request
        async def local(response):
            response.headers["X-Async"] = "yes"
            return response

        return "ok"

    assert app.test_client().get("/").headers["x-async"] == "yes"
    assert seen == [("/", None)]
    assert not module.has_request_context() and not module.has_app_context()


def test_teardown_failure_does_not_leak_context():
    from higuma import has_app_context, has_request_context

    app = Higuma(__name__, static_folder=None)

    @app.teardown_request
    def teardown(error):
        raise ValueError("cleanup failed")

    with pytest.raises(ValueError, match="cleanup failed"), app.test_request_context():
        pass
    assert not has_request_context() and not has_app_context()


def test_direct_request_context_requires_request_and_no_session_middleware_is_run():
    from higuma import session

    app = Higuma(__name__, static_folder=None)
    with app.request_context(Request({"path": "/manual"})):
        assert higuma_request.path == "/manual"
        with pytest.raises(RuntimeError, match="SessionMiddleware"):
            session["x"] = 1
    with pytest.raises(TypeError), app.request_context({}):
        pass


def test_client_external_redirect_rejection(framework):
    module, app = framework

    @app.get("/external")
    def external():
        return module.redirect("https://other.example/")

    client = app.test_client()
    with pytest.raises(RuntimeError):
        client.get("/external", follow_redirects=True)


def test_client_relative_redirects_resolve_against_the_current_url():
    from higuma import redirect

    app = Higuma(__name__, static_folder=None)

    @app.get("/folder/start")
    def start():
        return redirect("next")

    @app.get("/folder/next")
    def next_page():
        return {"scheme": higuma_request.scheme}

    assert app.test_client().get(
        "/folder/start", base_url="https://example.test", follow_redirects=True
    ).get_json() == {"scheme": "https"}


def test_blueprint_hook_scope_order_and_error_isolation(framework):
    module, app = framework
    blueprint = module.Blueprint("pages", __name__, url_prefix="/pages")
    seen = []

    @app.before_request
    def before():
        seen.append("app-before")

    @blueprint.before_request
    def bp_before():
        seen.append("bp-before")

    @app.after_request
    def after(response):
        seen.append("app-after")
        return response

    @blueprint.after_request
    def bp_after(response):
        seen.append("bp-after")
        response.headers["X-Blueprint"] = "pages"
        return response

    blueprint.teardown_request(lambda error: seen.append("bp-teardown"))
    app.teardown_request(lambda error: seen.append("app-teardown"))

    @blueprint.get("/known")
    def known():
        module.abort(404)

    @blueprint.errorhandler(404)
    def bp_missing(error):
        return "blueprint missing", 404

    @app.errorhandler(404)
    def app_missing(error):
        return "app missing", 404

    app.register_blueprint(blueprint)
    client = app.test_client()
    response = client.get("/pages/known")
    assert response.get_data(as_text=True) == "blueprint missing"
    assert response.headers["X-Blueprint"] == "pages"
    assert seen == [
        "app-before",
        "bp-before",
        "bp-after",
        "app-after",
        "bp-teardown",
        "app-teardown",
    ]
    seen.clear()
    response = client.get("/pages/missing")
    assert response.get_data(as_text=True) == "app missing"
    assert "X-Blueprint" not in response.headers
    assert seen == ["app-before", "app-after", "app-teardown"]


def test_blueprint_error_code_precedes_generic_exception_handler(framework):
    module, app = framework
    blueprint = module.Blueprint("pages", __name__)

    @blueprint.get("/")
    def index():
        module.abort(404)

    blueprint.register_error_handler(Exception, lambda error: ("generic bp", 500))
    app.register_error_handler(404, lambda error: ("specific app", 404))
    app.register_blueprint(blueprint)
    response = app.test_client().get("/")
    assert response.status_code == 404
    assert response.get_data(as_text=True) == "specific app"


def test_blueprint_context_processors_apply_to_both_template_helpers(framework, tmp_path):
    module, _ = framework
    (tmp_path / "page.html").write_text("{{ brand }}|{{ scope }}|{{ name }}", encoding="utf-8")
    app = getattr(module, "Higuma", getattr(module, "Flask", None))(
        __name__, template_folder=str(tmp_path), static_folder=None
    )
    blueprint = module.Blueprint("pages", __name__, url_prefix="/pages")
    app.context_processor(lambda: {"brand": "global", "scope": "app", "name": "default"})
    blueprint.context_processor(lambda: {"brand": "blueprint", "scope": "local"})

    @blueprint.get("/template")
    def template():
        return module.render_template("page.html", name="explicit")

    @app.get("/template")
    def app_template():
        return module.render_template("page.html", name="explicit")

    app.register_blueprint(blueprint)
    client = app.test_client()
    assert client.get("/pages/template").get_data(as_text=True) == "blueprint|local|explicit"
    assert client.get("/template").get_data(as_text=True) == "global|app|explicit"
    with app.test_request_context("/pages/template"):
        values = {"brand": "explicit"}
        app.update_template_context(values)
        assert {key: values[key] for key in ("brand", "scope", "name")} == {
            "brand": "explicit",
            "scope": "local",
            "name": "default",
        }


def test_http_error_handler_failure_returns_500_and_teardown_receives_failure(framework):
    module, app = framework
    seen = []
    app.teardown_request(lambda error: seen.append(error))
    failure = ValueError("must-not-leak")

    @app.errorhandler(404)
    def broken(error):
        raise failure

    response = app.test_client().get("/missing")
    assert response.status_code == 500
    assert "must-not-leak" not in response.get_data(as_text=True)
    assert seen == [failure]
    assert not module.has_request_context() and not module.has_app_context()


def test_blueprint_prefixes_and_invalid_namespace_are_guarded():
    from higuma import Blueprint

    app = Higuma(__name__, static_folder=None)
    blueprint = Blueprint("pages", __name__)
    seen = []
    blueprint.before_request(lambda: seen.append(higuma_request.blueprint))

    @blueprint.get("/")
    def index():
        return "ok"

    app.register_blueprint(blueprint, url_prefix="/one", name_prefix="one.")
    app.register_blueprint(blueprint, url_prefix="/two", name_prefix="two.")
    client = app.test_client()
    assert client.get("/one").status_code == 200
    assert client.get("/two").status_code == 200
    assert seen == ["one.pages", "two.pages"]
    with pytest.raises(ValueError, match="already registered"):
        app.register_blueprint(blueprint, url_prefix="/other", name_prefix="one.")
    for name in ("", "bad.name"):
        with pytest.raises(ValueError):
            Blueprint(name, __name__)
    with pytest.raises(ValueError):
        blueprint.get("/bad", endpoint="bad.name")(lambda: "bad")
    with pytest.raises(ValueError):
        blueprint.websocket("/bad", endpoint="bad.name")(lambda ws: None)


def test_non_application_json_suffix_is_not_json(framework):
    module, app = framework

    @app.post("/")
    def index():
        return {"data": module.request.get_json()}

    assert (
        app.test_client().post("/", data="{}", content_type="text/custom+json").status_code == 415
    )


def test_session_survives_response_replacement_and_handled_error(framework):
    module, app = framework
    if module.__name__ == "flask":
        app.secret_key = "session-replacement-test-secret-at-least-32-bytes"
    else:
        app.add_middleware(SessionMiddleware, "session-replacement-test-secret-at-least-32-bytes")
    seen = []
    app.teardown_request(lambda error: seen.append(error))

    @app.after_request
    def replace(response):
        if module.request.path == "/write":
            module.session["saved"] = True
            return module.Response("replacement", headers={"Vary": "Accept-Encoding"})
        return response

    @app.get("/write")
    def write():
        module.abort(400)

    @app.get("/read")
    def read():
        return dict(module.session)

    client = app.test_client()
    response = client.get("/write")
    assert response.get_data(as_text=True) == "replacement"
    assert {item.strip().lower() for item in response.headers["Vary"].split(",")} == {
        "accept-encoding",
        "cookie",
    }
    assert client.get("/read").get_json() == {"saved": True}
    assert seen == [None, None]


@pytest.mark.parametrize("status", [303, 307, 308])
def test_redirect_method_and_body_preservation(status):
    from higuma import redirect

    app = Higuma(__name__, static_folder=None)

    @app.post("/start")
    def start():
        return redirect("/target", status)

    @app.route("/target", methods=["GET", "POST"])
    def target():
        return {
            "method": higuma_request.method,
            "body": higuma_request.get_data(as_text=True),
            "content_type": higuma_request.headers.get("content-type"),
        }

    response = app.test_client().post(
        "/start",
        data="payload",
        headers={"Content-Type": "text/plain", "Content-Length": "7"},
        follow_redirects=True,
    )
    assert len(response.history) == 1
    assert response.get_json() == (
        {"method": "GET", "body": "", "content_type": None}
        if status == 303
        else {"method": "POST", "body": "payload", "content_type": "text/plain"}
    )


def test_context_request_builder_handles_forms_files_and_argument_conflicts():
    app = Higuma(__name__, static_folder=None)
    with app.test_request_context(
        "/upload", method="POST", data={"name": "bear"}, files={"file": ("bear.txt", b"hello")}
    ) as built:
        assert isinstance(built, Request) and built.path == higuma_request.path
        assert built.form["name"] == "bear"
        assert built.files["file"].read() == b"hello"
        assert built.endpoint is None
    client = app.test_client()
    for kwargs in (
        {"query": {"a": 1}, "query_string": "a=2"},
        {"json": {}, "data": "body"},
        {"base_url": "https://example.test/prefix"},
        {"base_url": "https://user:password@example.test"},
    ):
        with pytest.raises(ValueError):
            client.get("/", **kwargs)
