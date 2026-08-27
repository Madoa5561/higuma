from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Iterable
from contextvars import copy_context
from threading import Thread
from typing import Any


class BackgroundTask:
    def __init__(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        if not callable(func):
            raise TypeError("background task func must be callable")
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self._context = copy_context()

    def __call__(self) -> None:
        self._context.run(self._run)

    def _run(self) -> None:
        _resolve_awaitable(self.func(*self.args, **self.kwargs))


class BackgroundTasks:
    def __init__(self, tasks: Iterable[BackgroundTask] = ()) -> None:
        self.tasks = list(tasks)
        if not all(isinstance(task, BackgroundTask) for task in self.tasks):
            raise TypeError("tasks must contain BackgroundTask instances")

    def add_task(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        self.tasks.append(BackgroundTask(func, *args, **kwargs))

    def __bool__(self) -> bool:
        return bool(self.tasks)

    def __call__(self) -> None:
        for task in self.tasks:
            task()


def _resolve_awaitable(value: Any) -> Any:
    if not inspect.isawaitable(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)

    result: list[Any] = []
    errors: list[BaseException] = []
    context = copy_context()

    def runner() -> None:
        try:
            result.append(context.run(asyncio.run, value))
        except BaseException as exc:  # noqa: BLE001 - propagate task failure
            errors.append(exc)

    thread = Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    return result[0]
