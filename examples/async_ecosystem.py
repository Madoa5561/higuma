"""ASGI/async SQLite demo. The runnable factory uses a disposable in-memory DB."""

from dataclasses import dataclass
from typing import Annotated

from higuma import AsyncSQLAlchemyExtension, BadRequest, Body, HealthChecks, Higuma, Observability
from sqlalchemy import text


@dataclass
class Note:
    title: str


def create_app(database_url: str, *, initialize=False):
    app = Higuma(__name__, static_folder=None)
    database = AsyncSQLAlchemyExtension()
    database.init_app(app, database_url)
    health = HealthChecks(timeout=1)
    health.install(app)
    observer = Observability()
    app.extensions["observability"] = observer

    if initialize:

        @app.on_startup
        async def create_demo_table():
            with app.app_context():
                async with database.engine.begin() as connection:
                    await connection.execute(
                        text("create table if not exists notes (title text not null)")
                    )

    async def database_ready():
        with app.app_context():
            async with database.transaction() as session:
                return await session.scalar(text("select 1")) == 1

    health.add_check("database", database_ready)

    @app.get("/notes")
    async def notes():
        async with database.transaction() as session:
            return {"titles": list((await session.scalars(text("select title from notes"))).all())}

    @app.post("/notes", status_code=201)
    async def create_note(payload: Annotated[Note, Body()]):
        if not 1 <= len(payload.title) <= 200:
            raise BadRequest(detail="title must contain 1 to 200 characters")
        async with database.transaction() as session:
            await session.execute(
                text("insert into notes (title) values (:title)"), {"title": payload.title}
            )
        return {"title": payload.title}

    return app


def create_asgi_app():
    app = create_app("sqlite+aiosqlite://", initialize=True)
    return app.extensions["observability"].wrap_asgi(app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_asgi_app(), host="127.0.0.1", port=8000, lifespan="on")
