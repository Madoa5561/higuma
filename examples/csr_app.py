from pathlib import Path

from higuma import Higuma, __version__, redirect


def create_app() -> Higuma:
    app = Higuma(__name__, static_folder=None)

    @app.get("/")
    def index():
        return redirect("/app/")

    @app.get("/api/status")
    def api_status():
        return {"framework": "higuma", "version": __version__, "status": "ok"}

    app.mount_spa("/app", Path(__file__).parent / "frontend", name="client")
    return app


if __name__ == "__main__":
    create_app().run()
