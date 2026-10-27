import asyncio
import os
import uuid
from typing import Annotated, Any

import pytest
from higuma import Depends, Higuma, RedisExtension, SQLAlchemyExtension


def test_sqlalchemy_transactions_dependencies_rollback_and_app_isolation(tmp_path):
    sqlalchemy = pytest.importorskip("sqlalchemy")
    database = SQLAlchemyExtension()
    app = Higuma(__name__, static_folder=None)
    database.init_app(app, "sqlite:///" + str(tmp_path / "notes.sqlite"))
    metadata = sqlalchemy.MetaData()
    notes = sqlalchemy.Table(
        "notes",
        metadata,
        sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
        sqlalchemy.Column("name", sqlalchemy.String, unique=True),
    )
    with app.app_context():
        metadata.create_all(database.engine)
        with database.transaction() as db:
            db.execute(notes.insert().values(name="saved"))

    @app.get("/")
    def index(db: Annotated[Any, Depends(database.session)]):
        return {"names": list(db.scalars(sqlalchemy.select(notes.c.name)))}

    @app.post("/fail")
    def fail():
        with database.transaction() as db:
            db.execute(notes.insert().values(name="rolled-back"))
            raise ValueError("must roll back")

    app.register_error_handler(ValueError, lambda error: ("rolled back", 409))
    with app.test_client() as client:
        assert client.get("/").get_json() == {"names": ["saved"]}
        assert client.post("/fail").status_code == 409
        assert client.get("/").get_json() == {"names": ["saved"]}

        async def exercise():
            async with app.test_async_client() as asynchronous:
                assert (await asynchronous.get("/")).get_json() == {"names": ["saved"]}

        asyncio.run(exercise())
    other = Higuma(__name__, static_folder=None)
    database.init_app(other, "sqlite://")
    assert app.extensions[database.key]["engine"] is not other.extensions[database.key]["engine"]
    with pytest.raises(ValueError, match="already initialized"):
        database.init_app(other, "sqlite://")
    with other.app_context(), pytest.raises(RuntimeError, match="initialize"):
        _ = SQLAlchemyExtension().engine
    app.extensions[database.key]["engine"].dispose()
    other.extensions[database.key]["engine"].dispose()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_redis_real_round_trip_and_lifespan_close(asynchronous):
    pytest.importorskip("redis")
    url = os.getenv("HIGUMA_TEST_REDIS_URL")
    if not url:
        pytest.skip("set HIGUMA_TEST_REDIS_URL to an isolated test Redis")
    app = Higuma(__name__, static_folder=None)
    cache = RedisExtension()
    cache.init_app(app, url, async_client=asynchronous, decode_responses=True)
    key = "higuma-tests:" + uuid.uuid4().hex
    if asynchronous:

        @app.get("/")
        async def index():
            client = cache.client()
            await client.set(key, "日本", ex=30)
            try:
                return {"value": await client.get(key)}
            finally:
                await client.delete(key)
    else:

        @app.get("/")
        def index():
            client = cache.client()
            client.set(key, "日本", ex=30)
            try:
                return {"value": client.get(key)}
            finally:
                client.delete(key)

    async def exercise():
        async with app.test_async_client() as client:
            assert (await client.get("/")).get_json() == {"value": "日本"}
        assert app.extensions[cache.key]["client"] is None

    asyncio.run(exercise())


def test_redis_startup_error_closes_unpublished_client(monkeypatch):
    redis = pytest.importorskip("redis.asyncio")
    closed = []

    class FailedClient:
        async def ping(self):
            raise RuntimeError("unavailable")

        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(redis.Redis, "from_url", lambda *args, **kwargs: FailedClient())
    app = Higuma(__name__, static_folder=None)
    cache = RedisExtension()
    cache.init_app(app, "redis://127.0.0.1:1/0", async_client=True)

    async def exercise():
        with pytest.raises(RuntimeError, match="lifecycle failed"):
            async with app.test_async_client():
                pass

    asyncio.run(exercise())
    assert closed == [True] and app.extensions[cache.key]["client"] is None


def test_redis_counter_example_real_communication():
    pytest.importorskip("redis")
    url = os.getenv("HIGUMA_TEST_REDIS_URL")
    if not url:
        pytest.skip("set HIGUMA_TEST_REDIS_URL to an isolated test Redis")
    from examples.redis_counter import create_app

    prefix = "higuma-example-tests:" + uuid.uuid4().hex + ":"
    app = create_app(url, key_prefix=prefix)

    async def exercise():
        async with app.test_async_client() as client:
            try:
                assert (await client.post("/counter/bear")).get_json() == {
                    "name": "bear",
                    "value": 1,
                }
                assert (await client.post("/counter/bear")).get_json()["value"] == 2
                assert (await client.post("/counter/bad.name")).status_code == 400
            finally:
                with app.app_context():
                    cache = app.extensions[RedisExtension.key]["owner"]
                    await cache.client().delete(prefix + "bear")

    asyncio.run(exercise())


@pytest.mark.parametrize("transport", ["native", "asgi"])
def test_partial_startup_runs_all_resource_cleanup_even_after_shutdown_failure(transport):
    app = Higuma(__name__, static_folder=None)
    events = []
    app.on_startup(lambda: events.append("started"))

    def broken_start():
        raise ValueError("startup failed")

    app.on_startup(broken_start)
    app.on_shutdown(lambda: events.append("closed"))

    def broken_stop():
        events.append("shutdown-error")
        raise ValueError("shutdown failed")

    app.on_shutdown(broken_stop)
    if transport == "native":
        with pytest.raises(ValueError, match="shutdown failed"), app.lifespan():
            pass
    else:

        async def exercise():
            with pytest.raises(RuntimeError, match="lifecycle failed"):
                async with app.test_async_client():
                    pass

        asyncio.run(exercise())
    assert events == ["started", "shutdown-error", "closed"]
