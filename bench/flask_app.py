from flask import Flask

app = Flask(__name__)


@app.get("/ping")
def ping():
    return "pong"


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8011, debug=False, use_reloader=False, threaded=True)
