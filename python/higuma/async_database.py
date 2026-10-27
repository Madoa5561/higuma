from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager


class AsyncSQLAlchemyExtension:
    """ASGI-loop-owned engines; sessions never commit implicitly after a response."""

    key = "higuma.sqlalchemy.async"

    def init_app(self, app, url: str, **engine_options):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        if self.key in app.extensions:
            raise ValueError("async SQLAlchemy integration is already initialized")
        state = {"owner": self, "engine": None, "sessions": None, "loop": None}
        app.extensions[self.key] = state

        @app.on_startup
        async def start():
            if state["engine"] is not None:
                raise RuntimeError("async database is already started")
            engine = create_async_engine(url, **engine_options)
            state.update(
                engine=engine,
                sessions=async_sessionmaker(engine, expire_on_commit=False),
                loop=asyncio.get_running_loop(),
            )

        @app.on_shutdown
        async def stop():
            engine = state["engine"]
            if engine is not None:
                if state["loop"] is not asyncio.get_running_loop():
                    raise RuntimeError("dispose the async database on its owning ASGI loop")
                try:
                    await engine.dispose()
                finally:
                    state.update(engine=None, sessions=None, loop=None)

    def _state(self):
        from .app import current_app

        state = current_app.extensions.get(self.key)
        if state is None or state["owner"] is not self or state["engine"] is None:
            raise RuntimeError("initialize and start the async SQLAlchemy extension first")
        if state["loop"] is not asyncio.get_running_loop():
            raise RuntimeError("async database must be used on its owning ASGI loop")
        return state

    @property
    def engine(self):
        return self._state()["engine"]

    async def session(self):
        async with self._state()["sessions"]() as session:
            yield session

    @asynccontextmanager
    async def transaction(self):
        async with self._state()["sessions"].begin() as session:
            yield session
