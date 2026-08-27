from time import perf_counter

from higuma import Blueprint, Higuma, current_app, request

api = Blueprint("api", __name__, url_prefix="/v1")


@api.get("/status", endpoint="status")
def status():
    return {
        "service": current_app.config["SERVICE_NAME"],
        "request_id": request.state["request_id"],
    }


def create_app() -> Higuma:
    app = Higuma(__name__)
    app.config.update(
        SERVICE_NAME="blueprint-hooks-example",
        MAX_PAGE_SIZE=100,
    )
    app.register_blueprint(api, name_prefix="public")

    @app.before_request
    def start_timer():
        request.state["started_at"] = perf_counter()
        request.state["request_id"] = request.headers.get("x-request-id", "local")

    @app.after_request
    def add_timing(response):
        elapsed = perf_counter() - request.state["started_at"]
        response.headers["server-timing"] = f"app;dur={elapsed * 1000:.2f}"
        return response

    @app.context_processor
    def common_template_context():
        return {"service_name": app.config["SERVICE_NAME"]}

    @app.on_startup
    def startup():
        app.config["READY"] = True

    @app.on_shutdown
    def shutdown():
        app.config["READY"] = False

    return app


if __name__ == "__main__":
    create_app().run()
