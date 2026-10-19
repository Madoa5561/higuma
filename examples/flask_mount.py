from flask import Flask, url_for
from higuma import Higuma


def create_app() -> Higuma:
    app = Higuma(__name__, static_folder=None)
    legacy = Flask(__name__)

    @legacy.get("/hello/<name>")
    def hello(name):
        return {"name": name, "self": url_for("hello", name=name)}

    app.mount_wsgi("/legacy", legacy)
    return app


if __name__ == "__main__":
    create_app().run()
