from contextlib import asynccontextmanager

from higuma import BackgroundTask, Higuma, JSONResponse


@asynccontextmanager
async def lifespan(app: Higuma):
    app.state["ready"] = True
    print("application started")
    try:
        yield
    finally:
        app.state["ready"] = False
        print("application stopped")


app = Higuma(__name__, lifespan=lifespan)


def audit_health_check() -> None:
    print("health response was sent")


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse(
        {"ready": app.state.get("ready", False)},
        background=BackgroundTask(audit_health_check),
    )


if __name__ == "__main__":
    app.run()
