import asyncio
import importlib
import importlib.util
import sys

import pytest
from higuma import Blueprint, Higuma, current_app, has_app_context, request, url_for


@pytest.mark.parametrize("framework", ["higuma", "flask"])
def test_nested_blueprint_lifecycle_matches_flask(framework):
    api = importlib.import_module(framework)
    app_class = api.Higuma if framework == "higuma" else api.Flask
    app = app_class(__name__, static_folder=None)
    parent = api.Blueprint("parent", __name__, url_prefix="/parent")
    child = api.Blueprint("child", __name__, url_prefix="/child")
    events = []

    def before(label):
        def hook():
            events.append("before:" + label)

        return hook

    def after(label):
        def hook(response):
            events.append("after:" + label)
            return response

        return hook

    for label, target in [("app", app), ("parent", parent), ("child", child)]:
        target.before_request(before(label))
        target.after_request(after(label))
        target.teardown_request(lambda error, label=label: events.append("teardown:" + label))

    @child.get("/")
    def index():
        return {"blueprint": api.request.blueprint, "link": api.url_for(".index")}

    parent.register_blueprint(child)
    app.register_blueprint(parent)
    result = app.test_client().get("/parent/child/")
    assert result.get_json() == {
        "blueprint": "parent.child",
        "link": "/parent/child/" if framework == "flask" else "/parent/child",
    }
    assert events == [
        "before:app",
        "before:parent",
        "before:child",
        "after:child",
        "after:parent",
        "after:app",
        "teardown:child",
        "teardown:parent",
        "teardown:app",
    ]


def test_nested_blueprint_resources_errors_and_aliases(tmp_path):
    templates, static = tmp_path / "templates", tmp_path / "static"
    templates.mkdir()
    static.mkdir()
    (templates / "page.html").write_text(
        "{{ parent }}:{{ child }}:{{ explicit }}", encoding="utf-8"
    )
    (static / "style.css").write_text("body{}", encoding="utf-8")
    parent = Blueprint("admin", __name__, url_prefix="/admin")
    child = Blueprint(
        "users",
        __name__,
        url_prefix="/users",
        root_path=tmp_path,
        template_folder="templates",
        static_folder="static",
    )
    app = Higuma(__name__, static_folder=None)
    parent.context_processor(lambda: {"parent": "parent", "explicit": "wrong"})
    child.context_processor(lambda: {"child": "child"})
    parent.register_error_handler(ValueError, lambda error: ("parent-error", 409))

    @child.get("/")
    def index():
        return app.render_template("page.html", explicit="right")

    @child.get("/fail")
    def fail():
        raise ValueError("example")

    parent.register_blueprint(child)
    app.register_blueprint(parent)
    app.register_blueprint(parent, name="other", url_prefix="/other")
    client = app.test_client()
    assert client.get("/admin/users").text == "parent:child:right"
    assert client.get("/admin/users/fail").status_code == 409
    assert client.get("/other/users/static/style.css").body == b"body{}"
    assert client.get("/admin/users/static/../page.html").status_code == 404
    with app.test_request_context("/other/users"):
        assert request.blueprint == "other.users"
        assert url_for(".static", filename="style.css") == "/other/users/static/style.css"


def test_blueprint_rejects_cycle_and_collision_before_registering():
    app = Higuma(__name__, static_folder=None)
    parent, child = Blueprint("parent", __name__), Blueprint("child", __name__)
    parent.register_blueprint(child)
    child.register_blueprint(parent)
    with pytest.raises(ValueError, match="cycle"):
        app.register_blueprint(parent)
    assert not app._blueprints
    bad = Blueprint("bad", __name__)
    bad.get("/same", endpoint="one")(lambda: "one")
    bad.get("/same", endpoint="two")(lambda: "two")
    count = len(app._routes)
    with pytest.raises(ValueError, match="duplicate route"):
        app.register_blueprint(bad)
    assert len(app._routes) == count and not app._blueprints


def test_cli_commands_flags_context_blueprints_and_failures(monkeypatch):
    from higuma.cli import main

    app = Higuma(__name__, static_folder=None)
    cleaned = []
    app.teardown_appcontext(lambda error: cleaned.append(error))

    @app.cli.command()
    def greet(name: str, *, count: int = 1, loud: bool = False):
        assert current_app._get_current() is app
        return (name.upper() if loud else name) * count

    parent, child = Blueprint("admin", __name__), Blueprint("users", __name__)

    @child.cli.command()
    async def status():
        await asyncio.sleep(0)
        assert has_app_context()
        return "ready"

    parent.register_blueprint(child)
    app.register_blueprint(parent)
    runner = app.test_cli_runner()
    result = runner.invoke(["greet", "bear", "--count", "2", "--loud"])
    assert result.exit_code == 0 and result.output.strip() == "BEARBEAR"
    assert runner.invoke(["admin", "users", "status"]).output.strip() == "ready"
    assert runner.invoke(["greet", "bear", "--count", "bad"]).exit_code == 2
    assert not has_app_context() and len(cleaned) == 2
    monkeypatch.setattr(sys.modules[__name__], "cli_app", app, raising=False)
    assert main(["--app", __name__ + ":cli_app", "greet", "bear"]) == 0
    assert runner.invoke(["routes"]).exit_code == 0


def test_cli_command_works_with_postponed_annotations_and_local_return_type(tmp_path):
    module_path = tmp_path / "postponed_cli_app.py"
    module_path.write_text(
        "from __future__ import annotations\n"
        "from higuma import Higuma\n"
        "def create_app():\n"
        "    class LocalResult:\n"
        "        pass\n"
        "    app = Higuma(__name__, static_folder=None)\n"
        "    @app.cli.command()\n"
        "    def count(times: int = 2) -> LocalResult | str:\n"
        "        return 'x' * times\n"
        "    return app\n",
        encoding="utf-8",
    )
    spec = importlib.util.spec_from_file_location("postponed_cli_app", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = module.create_app()
    result = app.test_cli_runner().invoke("count --times 4")
    assert result.exit_code == 0 and result.output.strip() == "xxxx"


def test_cli_load_app_and_blueprint_group_collisions():
    from higuma.cli import build_parser, load_app

    for bad_ref in ("", "   ", ":app", "higuma:"):
        with pytest.raises(ValueError, match="application reference"):
            load_app(bad_ref)

    app = Higuma(__name__, static_folder=None)
    reserved = Blueprint("run", __name__)

    @reserved.cli.command()
    def ping():
        return "pong"

    app.register_blueprint(reserved)
    with pytest.raises(ValueError, match="conflicts"):
        build_parser(app)

    app2 = Higuma(__name__, static_folder=None)

    @app2.cli.command()
    def tools():
        return "top"

    bp = Blueprint("tools", __name__)

    @bp.cli.command()
    def sub():
        return "sub"

    app2.register_blueprint(bp)
    with pytest.raises(ValueError, match="conflicts"):
        build_parser(app2)


def test_cli_dotted_load_app_and_nested_blueprint_command_collision(tmp_path, monkeypatch):
    from higuma.cli import build_parser, load_app

    module_file = tmp_path / "dotted_app_mod.py"
    module_file.write_text(
        "from higuma import Higuma\n"
        "class Holder:\n"
        "    app = Higuma(__name__, static_folder=None)\n"
        "    @classmethod\n"
        "    def create(cls):\n"
        "        return cls.app\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    assert isinstance(load_app("dotted_app_mod:Holder.app"), Higuma)
    assert isinstance(load_app("dotted_app_mod:Holder.create()"), Higuma)
    with pytest.raises(ValueError, match="invalid object path"):
        load_app("dotted_app_mod:Holder..app")

    app = Higuma(__name__, static_folder=None)
    parent = Blueprint("admin", __name__)
    child = Blueprint("users", __name__)

    @parent.cli.command()
    def users():
        return "parent command"

    @child.cli.command()
    def list_all():
        return "child command"

    parent.register_blueprint(child)
    app.register_blueprint(parent)
    with pytest.raises(ValueError, match="conflicts"):
        build_parser(app)
