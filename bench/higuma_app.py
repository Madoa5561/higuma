from higuma import Higuma

app = Higuma(__name__)


@app.get("/ping")
def ping():
    return "pong"


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8010, workers=0)
