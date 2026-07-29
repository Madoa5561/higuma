from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any


@dataclass
class _DeferredRoute:
    rule: str
    methods: tuple[str, ...]
    endpoint: str
    view_func: Callable[..., Any]
    options: dict[str, Any]


class Blueprint:
    def __init__(
        self,
        name: str,
        import_name: str,
        *,
        url_prefix: str = "",
    ) -> None:
        if "." in name:
            raise ValueError("blueprint names cannot contain a dot")
        self.name = name
        self.import_name = import_name
        self.url_prefix = url_prefix.rstrip("/")
        self._routes: list[_DeferredRoute] = []

    def route(
        self,
        rule: str,
        *,
        methods: Iterable[str] | None = None,
        endpoint: str | None = None,
        **options: Any,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        method_tuple = tuple(method.upper() for method in (methods or ("GET",)))

        def decorator(view_func: Callable[..., Any]) -> Callable[..., Any]:
            self._routes.append(
                _DeferredRoute(
                    rule=rule,
                    methods=method_tuple,
                    endpoint=endpoint or view_func.__name__,
                    view_func=view_func,
                    options=options,
                )
            )
            return view_func

        return decorator

    def get(self, rule: str, **options: Any):
        return self.route(rule, methods=("GET",), **options)

    def post(self, rule: str, **options: Any):
        return self.route(rule, methods=("POST",), **options)

    def put(self, rule: str, **options: Any):
        return self.route(rule, methods=("PUT",), **options)

    def patch(self, rule: str, **options: Any):
        return self.route(rule, methods=("PATCH",), **options)

    def delete(self, rule: str, **options: Any):
        return self.route(rule, methods=("DELETE",), **options)
