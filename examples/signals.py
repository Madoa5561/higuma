"""Lifecycle subscriptions are scoped to this app; do not log template context or cookies."""

from higuma import Higuma, request_finished, request_started

app = Higuma(__name__, static_folder=None)


@request_started.connect_via(app)
def started(sender):
    sender.logger.debug("request started")


@request_finished.connect_via(app)
def finished(sender, response):
    sender.logger.debug("request finished: status=%s", response.status_code)


@app.get("/")
def index():
    return {"signals": True}


if __name__ == "__main__":
    app.run()
