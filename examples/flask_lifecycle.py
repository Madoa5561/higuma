import os
import time

from higuma import (
    Blueprint,
    CSRFProtection,
    Higuma,
    SessionMiddleware,
    abort,
    after_this_request,
    csrf_token,
    g,
    request,
    session,
)


def create_app() -> Higuma:
    app = Higuma(__name__, static_folder=None)
    app.add_middleware(SessionMiddleware, os.environ["HIGUMA_SECRET_KEY"])
    app.add_middleware(CSRFProtection)
    counter = Blueprint("counter", __name__, url_prefix="/counter")

    @counter.before_request
    def start_timer():
        g.started = time.perf_counter()

    @counter.after_request
    def timing(response):
        started = g.get("started")
        if started is not None:
            elapsed_ms = (time.perf_counter() - started) * 1000
            response.headers["Server-Timing"] = f"application;dur={elapsed_ms:.2f}"
        return response

    @counter.teardown_request
    def cleanup(error):
        g.pop("started", None)

    @counter.errorhandler(400)
    def bad_input(error):
        return {"error": "increment must be an integer between 1 and 10"}, 400

    @counter.get("/")
    def read():
        return {"count": session.get("count", 0), "csrf_token": csrf_token()}

    @counter.post("/increment")
    def increment():
        payload = request.get_json()
        amount = payload.get("increment") if isinstance(payload, dict) else None
        if not isinstance(amount, int) or isinstance(amount, bool) or not 1 <= amount <= 10:
            abort(400)
        next_count = session.get("count", 0) + amount

        @after_this_request
        def save(response):
            session["count"] = next_count
            response.headers["X-Counter-Saved"] = "yes"
            return response

        return {"count": next_count}

    app.register_blueprint(counter)
    return app


if __name__ == "__main__":
    create_app().run()
