import asyncio
from typing import Annotated, Any

import pytest
from higuma import AsyncSQLAlchemyExtension, Depends, Higuma


def test_async_database_explicit_commit_rollback_di_and_shutdown(tmp_path):
    sqlalchemy = pytest.importorskip("sqlalchemy")
    pytest.importorskip("aiosqlite")
    database = AsyncSQLAlchemyExtension()
    app = Higuma(__name__, static_folder=None)
    database.init_app(app, "sqlite+aiosqlite:///" + str(tmp_path / "async.sqlite"))

    @app.on_startup
    async def schema():
        with app.app_context():
            async with database.engine.begin() as connection:
                await connection.execute(sqlalchemy.text("create table notes (value text)"))

    @app.post("/write")
    async def write():
        async with database.transaction() as session:
            await session.execute(sqlalchemy.text("insert into notes values ('saved')"))
        return {"ok": True}

    @app.post("/rollback")
    async def rollback():
        try:
            async with database.transaction() as session:
                await session.execute(sqlalchemy.text("insert into notes values ('rolled back')"))
                raise ValueError("abort")
        except ValueError:
            return {"ok": False}

    @app.get("/")
    async def values(session: Annotated[Any, Depends(database.session)]):
        return {
            "values": list(
                (await session.scalars(sqlalchemy.text("select value from notes"))).all()
            )
        }

    async def exercise():
        async with app.test_async_client() as client:
            assert (await client.post("/write")).get_json() == {"ok": True}
            assert (await client.post("/rollback")).get_json() == {"ok": False}
            assert (await client.get("/")).get_json() == {"values": ["saved"]}
        assert app.extensions[database.key]["engine"] is None

    asyncio.run(exercise())


def test_async_database_cancellation_rolls_back_before_disconnect_completes():
    pytest.importorskip("aiosqlite")
    from sqlalchemy import text
    from test_gateways import scope

    from examples.async_ecosystem import create_app

    app = create_app("sqlite+aiosqlite://", initialize=True)
    database = app.extensions[AsyncSQLAlchemyExtension.key]["owner"]
    started = None

    @app.post("/cancel")
    async def cancelled():
        async with database.transaction() as session:
            await session.execute(text("insert into notes values ('must rollback')"))
            started.set()
            await asyncio.sleep(60)

    async def exercise():
        nonlocal started
        started = asyncio.Event()
        async with app.test_async_client():
            incoming = asyncio.Queue()
            await incoming.put({"type": "http.request", "body": b""})

            async def send(event):
                raise AssertionError("cancelled request must not send")

            task = asyncio.create_task(
                app.asgi_app(scope("/cancel", method="POST"), incoming.get, send)
            )
            await asyncio.wait_for(started.wait(), 2)
            await incoming.put({"type": "http.disconnect"})
            await asyncio.wait_for(task, 2)
            with app.app_context():
                async with database.transaction() as session:
                    assert await session.scalar(text("select count(*) from notes")) == 0

    asyncio.run(exercise())
