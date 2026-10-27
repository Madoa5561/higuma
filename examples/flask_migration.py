"""Flask-style URL hooks and reusable Blueprint application hooks. No extra dependencies."""

from higuma import Blueprint, Higuma, abort, g, jsonify, request, url_for


def create_app():
    app = Higuma(__name__, static_folder=None)
    app.config.from_mapping(SITE_TITLE="Higuma", SITE_LANGUAGES=["ja", "en"])
    pages = Blueprint("pages", __name__, url_prefix="/<language>")

    @pages.url_value_preprocessor
    def select_language(endpoint, values):
        language = values.pop("language")
        if language not in app.config["SITE_LANGUAGES"]:
            abort(404)
        g.language = language

    @pages.url_defaults
    def default_language(endpoint, values):
        values.setdefault("language", g.get("language", "ja"))

    @pages.after_app_request
    def project_header(response):
        response.headers["X-Project"] = app.config["SITE_TITLE"]
        return response

    @pages.get("/hello/<name>")
    def hello(name):
        return {
            "language": g.language,
            "name": name,
            "next": url_for(".hello", name="bear"),
            "blueprints": request.blueprints,
        }

    @app.get("/")
    def index():
        return {
            "hello": url_for("pages.hello", name="bear"),
            "site": app.config.get_namespace("SITE_"),
        }

    @app.post("/echo")
    def echo():
        return jsonify(request.get_json(cache=False))

    app.register_blueprint(pages)
    return app


if __name__ == "__main__":
    create_app().run()
