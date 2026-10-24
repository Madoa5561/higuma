from __future__ import annotations

from collections.abc import Callable, Iterator, MutableMapping
from contextvars import ContextVar
from types import SimpleNamespace
from typing import Any

from .request import LocalProxy, _request_context


class _AppGlobals(SimpleNamespace):
    def get(self, name: str, default: Any = None) -> Any:
        return self.__dict__.get(name, default)

    def pop(self, name: str, default: Any = None) -> Any:
        return self.__dict__.pop(name, default)

    def setdefault(self, name: str, default: Any = None) -> Any:
        return self.__dict__.setdefault(name, default)

    def __contains__(self, name: str) -> bool:
        return name in self.__dict__

    def __iter__(self) -> Iterator[str]:
        return iter(self.__dict__)


_g_context: ContextVar[_AppGlobals | None] = ContextVar("higuma_g", default=None)
_app_context_error: ContextVar[BaseException | None] = ContextVar("higuma_app_error", default=None)


class _GlobalsProxy(LocalProxy[_AppGlobals]):
    def _get_current(self) -> _AppGlobals:
        value = _g_context.get()
        if value is None:
            raise RuntimeError("working outside of an application context")
        return value

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._get_current(), name, value)

    def __delattr__(self, name: str) -> None:
        delattr(self._get_current(), name)

    def __contains__(self, name: str) -> bool:
        return name in self._get_current()

    def __iter__(self) -> Iterator[str]:
        return iter(self._get_current())


class _SessionProxy(LocalProxy[Any], MutableMapping[str, Any]):
    def _get_current(self) -> Any:
        current = _request_context.get()
        if current is None:
            raise RuntimeError("working outside of a request context")
        value = getattr(current, "session", None)
        if value is None:
            raise RuntimeError("session requires SessionMiddleware")
        return value

    def __setitem__(self, key: str, value: Any) -> None:
        self._get_current()[key] = value

    def __delitem__(self, key: str) -> None:
        del self._get_current()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._get_current())

    def __len__(self) -> int:
        return len(self._get_current())

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._get_current(), name, value)


g = _GlobalsProxy()
session = _SessionProxy()


def has_request_context() -> bool:
    return _request_context.get() is not None


def has_app_context() -> bool:
    from .app import _app_context

    return _app_context.get() is not None


def after_this_request(func: Callable[..., Any]) -> Callable[..., Any]:
    current = _request_context.get()
    if current is None:
        raise RuntimeError("after_this_request requires a request context")
    current.state.setdefault("_higuma_after_request", []).append(func)
    return func


def url_for(endpoint: str, **values: Any) -> str:
    from .app import current_app

    return current_app.url_for(endpoint, **values)


def flash(message: Any, category: str = "message") -> None:
    messages = list(session.get("_flashes", ()))
    messages.append((category, message))
    session["_flashes"] = messages


def get_flashed_messages(
    with_categories: bool = False, category_filter: tuple[str, ...] = ()
) -> list[Any]:
    current = _request_context.get()
    if current is None:
        raise RuntimeError("working outside of a request context")
    if "_higuma_flashes" not in current.state:
        current.state["_higuma_flashes"] = session.pop("_flashes", [])
    messages = [
        (category, message)
        for category, message in current.state["_higuma_flashes"]
        if not category_filter or category in category_filter
    ]
    return messages if with_categories else [message for _, message in messages]
