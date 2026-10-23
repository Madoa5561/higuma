import os
from pathlib import Path

from higuma import Higuma, ViteManifest


def create_app() -> Higuma:
    # Point this at your own Vite build; no Node process is started by higuma.
    directory = Path(os.environ.get("HIGUMA_FRONTEND_DIST", "frontend/dist")).resolve()
    manifest = ViteManifest(directory / ".vite/manifest.json", base_url="/build/")
    app = Higuma(__name__, static_folder=None)
    app.mount_static("/build", directory, name="build")

    @app.get("/")
    def index():
        return app.render_template("vite.html", vite_tags=manifest.tags("index.html"))

    return app


if __name__ == "__main__":
    create_app().run()
