import json
import sys
import types

import pytest
from higuma import Higuma, JSONResponse
from higuma.config import Config


@pytest.fixture(params=["higuma", "flask"])
def framework(request):
    module = pytest.importorskip(request.param)
    factory = module.Higuma if request.param == "higuma" else module.Flask
    return module, factory(__name__, static_folder=None)


def test_nested_url_hooks_follow_flask_order_and_mutate_view_args(framework):
    module, app = framework
    parent = module.Blueprint("parent", __name__, url_prefix="/<language>")
    child = module.Blueprint("child", __name__, url_prefix="/items")
    events = []

    @app.url_value_preprocessor
    def app_values(endpoint, values):
        events.append("app-values")

    @parent.url_value_preprocessor
    def parent_values(endpoint, values):
        events.append("parent-values")
        module.g.language = values.pop("language")

    @child.url_value_preprocessor
    def child_values(endpoint, values):
        events.append("child-values")
        values["item_id"] += 1

    @app.before_request
    def before():
        events.append("before")
        assert "language" not in module.request.view_args

    @app.url_defaults
    def app_defaults(endpoint, values):
        events.append("app-defaults")
        values.setdefault("source", "app")

    @parent.url_defaults
    def parent_defaults(endpoint, values):
        events.append("parent-defaults")
        values.setdefault("language", module.g.language)

    @child.url_defaults
    def child_defaults(endpoint, values):
        events.append("child-defaults")

    @child.get("/<int:item_id>")
    def detail(item_id):
        return {
            "id": item_id,
            "language": module.g.language,
            "blueprints": module.request.blueprints,
            "url": module.url_for(".detail", item_id=item_id),
        }

    parent.register_blueprint(child)
    app.register_blueprint(parent)
    response = app.test_client().get("/ja/items/2")
    assert response.get_json() == {
        "id": 3,
        "language": "ja",
        "blueprints": ["parent.child", "parent"],
        "url": "/ja/items/3?source=app",
    }
    assert events == [
        "app-values",
        "parent-values",
        "child-values",
        "before",
        "app-defaults",
        "parent-defaults",
        "child-defaults",
    ]


def test_url_preprocessor_receives_none_for_routing_errors(framework):
    _, app = framework
    seen = []

    @app.url_value_preprocessor
    def values(endpoint, arguments):
        seen.append((endpoint, arguments))

    @app.get("/known")
    def known():
        return "ok"

    client = app.test_client()
    assert client.get("/missing").status_code == 404
    assert client.post("/known").status_code == 405
    assert seen == [(None, None), (None, None)]


def test_blueprint_app_hooks_apply_once_per_app_and_to_unrelated_routes(framework):
    module, app = framework
    plugin = module.Blueprint("plugin", __name__)
    seen = []

    @plugin.before_app_request
    def before():
        seen.append("before")

    @plugin.after_app_request
    def after(response):
        seen.append("after")
        response.headers["X-Plugin"] = "yes"
        return response

    @plugin.teardown_app_request
    def cleanup(error):
        seen.append("teardown")

    @plugin.app_context_processor
    def context():
        return {"plugin": True}

    @plugin.app_url_defaults
    def defaults(endpoint, values):
        values.setdefault("plugin", "yes")

    @plugin.app_url_value_preprocessor
    def preprocess(endpoint, values):
        seen.append("values")

    @plugin.app_errorhandler(404)
    def missing(error):
        return {"error": "plugin-missing"}, 404

    app.register_blueprint(plugin, name="one", url_prefix="/one")
    app.register_blueprint(plugin, name="two", url_prefix="/two")

    @app.get("/outside")
    def outside():
        context = {}
        app.update_template_context(context)
        return {"plugin": context["plugin"], "url": module.url_for("outside")}

    client = app.test_client()
    response = client.get("/outside")
    assert response.get_json() == {"plugin": True, "url": "/outside?plugin=yes"}
    assert response.headers["X-Plugin"] == "yes"
    assert seen == ["values", "before", "after", "teardown"]
    assert client.get("/missing").get_json() == {"error": "plugin-missing"}

    other = type(app)(__name__, static_folder=None)
    other.register_blueprint(plugin)
    seen.clear()
    assert other.test_client().get("/missing").status_code == 404
    assert seen == ["values", "before", "after", "teardown"]


@pytest.mark.parametrize(
    "args,expected", [((), None), ((None,), None), ((1,), 1), ((1, 2), [1, 2])]
)
def test_jsonify_positional_arguments_match_flask(framework, args, expected):
    module, app = framework
    with app.app_context():
        assert module.jsonify(*args).get_json() == expected
        assert module.jsonify(name="bear").get_json() == {"name": "bear"}
        with pytest.raises(TypeError):
            module.jsonify(None, name="bear")


def test_request_json_cache_can_be_bypassed_without_replacing_cached_value(framework):
    module, app = framework
    with app.test_request_context("/", method="POST", json={"value": 1}):
        module.request.get_data()
        assert module.request.get_json(cache=False) == {"value": 1}
        cached = module.request.get_json()
        cached["value"] = 2
        assert module.request.get_json() is cached
        assert module.request.get_json(cache=False) == {"value": 1}
        assert module.request.get_json() == {"value": 2}


def test_empty_response_and_body_header_tuple(framework):
    module, app = framework
    with app.app_context():
        response = module.make_response()
        assert response.status_code == 200 and response.data == b""
        response = module.make_response(("body", {"X-Example": "yes"}))
        assert response.data == b"body" and response.headers["X-Example"] == "yes"
    response = app.make_response(("body", {"X-Example": "yes"}))
    assert response.headers["X-Example"] == "yes"


@pytest.fixture(params=["higuma.config", "flask.config"])
def configuration(request, tmp_path):
    module = pytest.importorskip(request.param)
    return module.Config(root_path=tmp_path)


def test_config_loader_root_namespace_and_silent(configuration, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"STORE_HOST":"localhost","STORE_PORT":1234}', encoding="utf-8")
    assert configuration.from_file("settings.json", load=json.load)
    assert configuration.get_namespace("STORE_") == {"host": "localhost", "port": 1234}
    assert configuration.get_namespace("STORE_", lowercase=False, trim_namespace=False) == {
        "STORE_HOST": "localhost",
        "STORE_PORT": 1234,
    }
    assert configuration.from_file("missing.json", load=json.load, silent=True) is False
    if sys.platform == "win32":
        with pytest.raises(PermissionError):
            configuration.from_file(".", load=json.load, silent=True)
    else:
        assert configuration.from_file(".", load=json.load, silent=True) is False
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(ValueError):
        configuration.from_file(path, load=json.load, silent=True)


def test_config_binary_loader(configuration, tmp_path):
    (tmp_path / "settings.bin").write_bytes(b"port=1234")

    def load(handle):
        assert handle.read() == b"port=1234"
        return {"PORT": 1234}

    assert configuration.from_file("settings.bin", load=load, text=False)
    assert configuration["PORT"] == 1234


def test_config_nested_environment_and_custom_loader(configuration, monkeypatch):
    monkeypatch.setenv("HIGUMA_MIGRATION_TEST_STORE__PORT", "1234")
    monkeypatch.setenv("HIGUMA_MIGRATION_TEST_STORE", '{"HOST":"local"}')
    monkeypatch.setenv("HIGUMA_MIGRATION_TEST_STORE__TLS__ENABLED", "true")
    assert configuration.from_prefixed_env("HIGUMA_MIGRATION_TEST")
    assert configuration["STORE"] == {"HOST": "local", "PORT": 1234, "TLS": {"ENABLED": True}}
    monkeypatch.setenv("HIGUMA_MIGRATION_LOADER_VALUE", "lowercase")
    assert configuration.from_prefixed_env("HIGUMA_MIGRATION_LOADER", loads=str.upper)
    assert configuration["VALUE"] == "LOWERCASE"


def test_config_imports_class_without_loading_private_attributes(configuration, monkeypatch):
    module = types.ModuleType("higuma_test_settings")
    module.Settings = type("Settings", (), {"ENABLED": True, "private": "not-loaded"})
    monkeypatch.setitem(sys.modules, module.__name__, module)
    assert configuration.from_object("higuma_test_settings.Settings") is not False
    assert configuration["ENABLED"] is True
    assert "private" not in configuration


def test_legacy_config_keys_and_default_json_loader_are_preserved(tmp_path, monkeypatch):
    config = Config(root_path=tmp_path)
    assert config.from_mapping({"lowercase": True})
    (tmp_path / "settings.json").write_text('{"lowercase":false}', encoding="utf-8")
    assert config.from_file("settings.json") and config["lowercase"] is False
    monkeypatch.setenv("HIGUMA_MIGRATION_CONFIG_FILE", str(tmp_path / "missing.json"))
    assert config.from_envvar("HIGUMA_MIGRATION_CONFIG_FILE", silent=True) is False
    assert "_root_path" not in config
    monkeypatch.setenv("HIGUMA_MIGRATION_COLLISION_VALUE", "3")
    monkeypatch.setenv("HIGUMA_MIGRATION_COLLISION_VALUE__NESTED", "4")
    with pytest.raises(TypeError, match="requires dictionaries"):
        config.from_prefixed_env("HIGUMA_MIGRATION_COLLISION")


def test_higuma_jsonify_retains_response_options():
    app = Higuma(__name__, static_folder=None)
    response = app.jsonify(None, status=202, headers={"X-Example": "yes"})
    assert response.status_code == 202 and response.get_json() is None
    assert response.headers["X-Example"] == "yes"
    assert app.jsonify(1, 2).get_json() == [1, 2]
    assert app.make_response().data == b""
    assert isinstance(app.jsonify(), JSONResponse)


def test_asgi_preprocessors_fall_back_and_are_not_skipped():
    import asyncio

    from higuma import g, request

    app = Higuma(__name__, static_folder=None)

    @app.url_value_preprocessor
    def values(endpoint, arguments):
        g.item = arguments.pop("item")

    @app.get("/<item>")
    async def index():
        return {"item": g.item, "fast": request.state.get("_higuma_async_fast_path", False)}

    async def run():
        async with app.test_async_client() as client:
            response = await client.get("/bear")
            assert response.get_json() == {"item": "bear", "fast": False}

    asyncio.run(run())


def test_async_url_callbacks_are_rejected_without_unawaited_coroutines():
    app = Higuma(__name__, static_folder=None)

    @app.get("/")
    def index():
        return "ok"

    @app.url_defaults
    async def defaults(endpoint, values):
        pass

    with pytest.raises(TypeError, match="synchronous"):
        app.url_for("index")


def test_config_class_import_preserves_missing_dependency_error(tmp_path, monkeypatch):
    path = tmp_path / "higuma_broken_test_settings.py"
    path.write_text("import higuma_missing_test_dependency\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(ModuleNotFoundError) as caught:
        Config().from_object("higuma_broken_test_settings.Settings")
    assert caught.value.name == "higuma_missing_test_dependency"


def test_example_rejects_unknown_language_and_keeps_request_values_isolated():
    from concurrent.futures import ThreadPoolExecutor

    from examples.flask_migration import create_app

    app = create_app()
    assert app.test_client().get("/unknown/hello/bear").status_code == 404

    def read(number):
        language = "ja" if number % 2 else "en"
        response = app.test_client().get(f"/{language}/hello/bear")
        assert response.get_json()["language"] == language
        assert response.get_json()["next"] == f"/{language}/hello/bear"

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(read, range(40)))


def test_response_header_tuple_still_rejects_header_injection():
    from higuma import make_response

    with pytest.raises(ValueError):
        make_response(("body", {"X-Example": "ok\r\nInjected: yes"}))


def test_config_silent_does_not_mask_loader_errors(tmp_path):
    import errno

    config = Config(root_path=tmp_path)
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")

    def load(handle):
        raise FileNotFoundError(errno.ENOENT, "loader input missing")

    with pytest.raises(FileNotFoundError, match="loader input missing"):
        config.from_file("settings.json", load=load, silent=True)
    with pytest.raises(TypeError, match="must return a mapping"):
        config.from_file("settings.json", load=lambda handle: [], silent=True)
