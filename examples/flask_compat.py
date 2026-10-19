import os

from higuma import (
    CSRFProtection,
    Higuma,
    SessionMiddleware,
    csrf_token,
    flash,
    g,
    get_flashed_messages,
    request,
    session,
    url_for,
)


def create_app() -> Higuma:
    secret = os.environ.get("HIGUMA_SECRET_KEY")
    if not secret:
        raise RuntimeError("set HIGUMA_SECRET_KEY to at least 32 random bytes")
    app = Higuma(__name__, static_folder=None)
    app.add_middleware(SessionMiddleware, secret)
    app.add_middleware(CSRFProtection)
    app.extensions["greeting"] = "Hello"

    @app.before_request
    def prepare_globals():
        g.greeting = app.extensions["greeting"]

    @app.get("/")
    def index():
        return {
            "greeting": g.greeting,
            "messages": get_flashed_messages(True),
            "visits": session.get("visits", 0),
            "csrf_token": csrf_token(),
            "save_url": url_for("save", source="example"),
        }

    @app.post("/save")
    def save():
        session["visits"] = session.get("visits", 0) + 1
        flash("Saved", "success")
        return {"next": url_for("index"), "endpoint": request.endpoint}

    return app


if __name__ == "__main__":
    create_app().run()
