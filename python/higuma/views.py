from __future__ import annotations

from collections.abc import Callable
from functools import update_wrapper
from typing import Any, ClassVar

from .exceptions import MethodNotAllowed
from .execution import resolve_awaitable
from .parameters import resolve_arguments
from .request import _request_context, request

_HTTP_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")


class View:
    methods: ClassVar[set[str] | None] = None
    decorators: ClassVar[tuple[Callable[[Callable[..., Any]], Callable[..., Any]], ...]] = ()
    init_every_request: ClassVar[bool] = True

    def dispatch_request(self, **kwargs: Any) -> Any:
        raise NotImplementedError

    @classmethod
    def as_view(cls, name: str, *class_args: Any, **class_kwargs: Any) -> Callable[..., Any]:
        if not name or not name.isidentifier():
            raise ValueError("view name must be a valid Python identifier")

        def invoke(instance: View, supplied: dict[str, Any]) -> Any:
            if type(instance).dispatch_request is MethodView.dispatch_request:
                return instance.dispatch_request(**supplied)
            current = _request_context.get()
            if current is None:
                return instance.dispatch_request(**supplied)
            args, resolved_kwargs = resolve_arguments(
                instance.dispatch_request,
                current,
                supplied or current.path_params,
                resolve_awaitable,
            )
            return instance.dispatch_request(*args, **resolved_kwargs)

        if cls.init_every_request:

            def view(**kwargs: Any) -> Any:
                return invoke(cls(*class_args, **class_kwargs), kwargs)

        else:
            instance = cls(*class_args, **class_kwargs)

            def view(**kwargs: Any) -> Any:
                return invoke(instance, kwargs)

        for decorator in cls.decorators:
            view = decorator(view)
        update_wrapper(view, cls, updated=())
        view.__dict__.pop("__wrapped__", None)
        view.__name__ = name
        view.methods = set(cls.methods or ())  # type: ignore[attr-defined]
        view.view_class = cls  # type: ignore[attr-defined]
        import inspect

        from .parameters import cache_parameter_hints

        frame = inspect.currentframe()
        try:
            cache_parameter_hints(
                view,
                frame.f_back.f_locals if frame is not None and frame.f_back is not None else {},
            )
        finally:
            del frame
        return view


class MethodView(View):
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "methods" not in cls.__dict__:
            cls.methods = {method for method in _HTTP_METHODS if hasattr(cls, method.lower())}

    def dispatch_request(self, **kwargs: Any) -> Any:
        method_name = request.method.lower()
        handler = getattr(self, method_name, None)
        if handler is None and request.method == "HEAD":
            handler = getattr(self, "get", None)
        if handler is None:
            allowed = sorted(self.methods or ())
            raise MethodNotAllowed(headers={"allow": ", ".join(allowed)})
        current = _request_context.get()
        if current is None:
            return handler(**kwargs)
        args, resolved_kwargs = resolve_arguments(
            handler,
            current,
            kwargs or current.path_params,
            resolve_awaitable,
        )
        return handler(*args, **resolved_kwargs)
