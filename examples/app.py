from higuma import Higuma

app = Higuma(__name__, template_folder="templates")


@app.get("/")
def index():
    return app.render_template(
        "index.html",
        title="higuma",
        message="Rust core + Python API",
        framework="higuma",
    )


@app.get("/ping")
def ping(_request):
    return "pong"


@app.post("/echo")
def echo(request):
    return app.jsonify(
        {
            "method": request["method"],
            "path": request["path"],
            "text": request["text"],
            "query": request["query"],
        }
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, workers=0)
