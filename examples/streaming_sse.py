import asyncio
from datetime import datetime, timezone

from higuma import (
    BackgroundTasks,
    EventSourceResponse,
    Higuma,
    ServerSentEvent,
    StreamingResponse,
    request,
)

app = Higuma(__name__)


@app.get("/stream")
def stream(background_tasks: BackgroundTasks) -> StreamingResponse:
    path = request.path

    def chunks():
        yield f"streaming {path}\n"
        yield "without buffering the whole response\n"

    background_tasks.add_task(print, f"completed {path}")
    return StreamingResponse(chunks(), media_type="text/plain; charset=utf-8")


@app.get("/events")
def events() -> EventSourceResponse:
    async def event_stream():
        for sequence in range(1, 4):
            yield ServerSentEvent(
                {"sequence": sequence, "at": datetime.now(timezone.utc)},
                event="tick",
                id=str(sequence),
                retry=3000,
            )
            await asyncio.sleep(1)

    return EventSourceResponse(event_stream())


if __name__ == "__main__":
    app.run()
