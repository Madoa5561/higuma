from __future__ import annotations

import asyncio
import inspect
from concurrent.futures import CancelledError, Future
from contextvars import ContextVar, copy_context
from threading import Lock, Thread, get_ident
from typing import Any


class _AwaitChannel:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.queue: asyncio.Queue[Any] = asyncio.Queue()
        self.task: asyncio.Task[Any] | None = None
        self.active: Future[Any] | None = None
        self.previous: dict[ContextVar[Any], Any] = {}
        self.completion = asyncio.run_coroutine_threadsafe(self._run(), loop)

    async def _run(self) -> None:
        self.task = asyncio.current_task()
        while True:
            command = await self.queue.get()
            if command is None:
                return
            value, context, future = command
            if future.cancelled():
                if inspect.iscoroutine(value):
                    value.close()
                continue
            for variable, item in context.items():
                if variable not in self.previous or self.previous[variable] is not item:
                    variable.set(item)
            self.previous = context
            self.active = future
            try:
                result = await value
            except BaseException as error:  # noqa: BLE001 - relay failures without losing the task context
                if isinstance(error, asyncio.CancelledError) and hasattr(self.task, "uncancel"):
                    while self.task.cancelling():
                        self.task.uncancel()
                if not future.done():
                    future.set_exception(error)
            else:
                if not future.done():
                    future.set_result(result)
            finally:
                self.active = None

    def submit(self, value: Any) -> Future[Any]:
        future: Future[Any] = Future()

        def cancel(done: Future[Any]) -> None:
            if done.cancelled():
                self.loop.call_soon_threadsafe(self._cancel, done)

        future.add_done_callback(cancel)
        self.loop.call_soon_threadsafe(
            self.queue.put_nowait, (value, dict(copy_context().items()), future)
        )
        return future

    def _cancel(self, future: Future[Any]) -> None:
        if self.active is future and self.task is not None:
            self.task.cancel()

    async def close(self) -> None:
        self.queue.put_nowait(None)
        await asyncio.wrap_future(self.completion)


class AwaitBridge:
    """Resolve worker-thread awaitables on the owning ASGI event loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self._pending: set[Future[Any]] = set()
        self._lock = Lock()
        self._cancel_requested = False
        self._channels: dict[int, _AwaitChannel] = {}
        self._closed = False

    def resolve(self, value: Any, *, channel: _AwaitChannel | None = None) -> Any:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            if inspect.iscoroutine(value):
                value.close()
            raise RuntimeError("use the async API when running on the ASGI event loop")
        with self._lock:
            if self._closed or self._cancel_requested:
                self._cancel_requested = False
                if inspect.iscoroutine(value):
                    value.close()
                raise asyncio.CancelledError()
            if channel is None:
                channel = self._channel()
            future = channel.submit(value)
            self._pending.add(future)
        try:
            return future.result()
        except CancelledError as error:
            raise asyncio.CancelledError() from error
        finally:
            with self._lock:
                self._pending.discard(future)

    def cancel_pending(self) -> None:
        with self._lock:
            pending = [future for future in self._pending if not future.done()]
            self._cancel_requested = not pending
        for future in pending:
            future.cancel()

    def _channel(self) -> _AwaitChannel:
        owner = get_ident()
        if owner not in self._channels:
            self._channels[owner] = _AwaitChannel(self.loop)
        return self._channels[owner]

    def bind(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("ASGI request context is closed")
            channel = self._channel()
        return lambda value: self.resolve(value, channel=channel)

    async def close(self) -> None:
        with self._lock:
            self._closed = True
            channels = list(self._channels.values())
        self.cancel_pending()
        for channel in channels:
            await channel.close()


await_bridge: ContextVar[AwaitBridge | None] = ContextVar("higuma_await_bridge", default=None)


def bind_await_resolver(fallback: Any) -> Any:
    bridge = await_bridge.get()
    return bridge.bind() if bridge is not None else fallback


async def _await(value: Any) -> Any:
    return await value


def resolve_awaitable(value: Any) -> Any:
    if not inspect.isawaitable(value):
        return value
    bridge = await_bridge.get()
    if bridge is not None:
        return bridge.resolve(value)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_await(value))
    results: list[Any] = []
    errors: list[BaseException] = []
    context = copy_context()

    def runner() -> None:
        try:
            results.append(context.run(asyncio.run, _await(value)))
        except BaseException as error:  # noqa: BLE001 - propagate worker cancellation and failures
            errors.append(error)

    thread = Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    return results[0]
