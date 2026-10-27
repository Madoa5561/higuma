from __future__ import annotations

from contextlib import contextmanager
from typing import Any


class SQLAlchemyExtension:
    """SQLAlchemy 2.x integration with isolated sessions and explicit commits."""

    key = "higuma.sqlalchemy"

    def init_app(self, app: Any, url: str, **engine_options: Any) -> None:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        if self.key in app.extensions:
            raise ValueError("SQLAlchemy integration is already initialized")
        engine = create_engine(url, **engine_options)
        app.extensions[self.key] = {
            "owner": self,
            "engine": engine,
            "sessions": sessionmaker(engine, expire_on_commit=False),
        }

        @app.on_shutdown
        def dispose():
            engine.dispose()

    def _state(self):
        from .app import current_app

        state = current_app.extensions.get(self.key)
        if state is None or state["owner"] is not self:
            raise RuntimeError("initialize this SQLAlchemy extension for the current app first")
        return state

    @property
    def engine(self):
        return self._state()["engine"]

    def session(self):
        # Closing rolls back uncommitted work. Never commit after a success response was sent.
        with self._state()["sessions"]() as database_session:
            yield database_session

    @contextmanager
    def transaction(self):
        with self._state()["sessions"].begin() as database_session:
            yield database_session


class RedisExtension:
    """Explicit, lifespan-managed redis-py clients; no hidden global connection."""

    key = "higuma.redis"

    def init_app(
        self,
        app: Any,
        url: str,
        *,
        async_client: bool = True,
        ping_on_startup: bool = True,
        **options: Any,
    ) -> None:
        if self.key in app.extensions:
            raise ValueError("Redis integration is already initialized")
        if not url.startswith(("redis://", "rediss://", "unix://")):
            raise ValueError("Redis URL must use redis, rediss, or unix")
        settings = {"socket_timeout": 5, "socket_connect_timeout": 5, "protocol": 2, **options}
        if async_client:
            from redis.asyncio import Redis
        else:
            from redis import Redis
        state = {"owner": self, "client": None}
        app.extensions[self.key] = state
        if async_client:

            @app.on_startup
            async def start():
                client = Redis.from_url(url, **settings)
                try:
                    if ping_on_startup:
                        await client.ping()
                except BaseException:
                    await client.aclose()
                    raise
                state["client"] = client

            @app.on_shutdown
            async def stop():
                client, state["client"] = state["client"], None
                if client is not None:
                    await client.aclose()
        else:

            @app.on_startup
            def start():
                client = Redis.from_url(url, **settings)
                try:
                    if ping_on_startup:
                        client.ping()
                except BaseException:
                    client.close()
                    raise
                state["client"] = client

            @app.on_shutdown
            def stop():
                client, state["client"] = state["client"], None
                if client is not None:
                    client.close()

    def client(self):
        from .app import current_app

        state = current_app.extensions.get(self.key)
        if state is None or state["owner"] is not self or state["client"] is None:
            raise RuntimeError("start this app's Redis lifespan before using its client")
        return state["client"]
