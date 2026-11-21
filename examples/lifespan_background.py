import asyncio
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


async def audit_health_check_async() -> None:
    await asyncio.sleep(0)
    print("async health response was sent")


@app.get("/health/async-background")
def health_async_background() -> JSONResponse:
    response = JSONResponse({"ready": app.state.get("ready", False)})
    response.background = audit_health_check_async
    return response


if __name__ == "__main__":
    app.run()
