import gc

import pytest
from higuma import (
    Higuma,
    Signal,
    template_rendered,
)


def test_signal_weak_receivers_sender_isolation_and_context_subscription():
    signal = Signal("test")
    first, second, calls = Higuma(__name__), Higuma(__name__), []

    def receiver(sender, **values):
        calls.append(values)

    with signal.connected_to(receiver, first):
        signal.send(second, value=0)
        signal.send(first, value=1)
        with signal.connected_to(receiver, first):
            signal.send(first, value=2)
        assert signal.has_receivers_for(first)
    assert calls == [{"value": 1}, {"value": 2}]
    assert not signal.has_receivers_for(first)

    class Listener:
        def receive(self, sender):
            pass

    listener = Listener()
    signal.connect(listener.receive, first)
    del listener
    gc.collect()
    assert not signal.has_receivers_for(first)


@pytest.mark.parametrize("framework", ["higuma", "flask"])
def test_request_signal_order_matches_flask(framework):
    module = pytest.importorskip(framework)
    app = (module.Higuma if framework == "higuma" else module.Flask)(__name__, static_folder=None)
    events = []

    def started(sender):
        events.append(("start", module.request.endpoint))

    def finished(sender, response):
        events.append(("finish", response.status_code))

    def teardown(sender, exc):
        events.append(("teardown", exc))

    @app.get("/")
    def index():
        events.append(("view", module.request.endpoint))
        return "bear"

    with (
        module.request_started.connected_to(started, app),
        module.request_finished.connected_to(finished, app),
        module.request_tearing_down.connected_to(teardown, app),
    ):
        assert (
            app.test_client().get("/").body == b"bear"
            if framework == "higuma"
            else app.test_client().get("/").data == b"bear"
        )
    assert events == [("start", "index"), ("view", "index"), ("finish", 200), ("teardown", None)]


def test_template_signal_fires_after_successful_render_only(tmp_path):
    (tmp_path / "page.html").write_text("Hello {{ value }}", encoding="utf-8")
    app = Higuma(__name__, static_folder=None, template_folder=str(tmp_path))
    seen = []

    @app.get("/")
    def index():
        return app.render_template("page.html", value="bear")

    def rendered(sender, template, context):
        seen.append((template, context["value"]))

    with template_rendered.connected_to(rendered, app):
        assert app.test_client().get("/").body == b"Hello bear"
    assert seen == [("page.html", "bear")]


def test_signal_none_sender_and_disconnect_all_preserve_temporary_scopes():
    signal = Signal("sender-edge-cases")
    app, seen = Higuma(__name__), []

    def callback(sender):
        seen.append(sender)

    signal.connect(callback, None)
    signal.send(None)
    signal.connect(callback, app)
    with signal.connected_to(callback):
        signal.send(app)
    assert signal.has_receivers_for(app)
    assert signal.has_receivers_for(None)
    signal.disconnect(callback)
    assert not signal.has_receivers_for(app) and not signal.has_receivers_for(None)
    assert seen == [None, app]
