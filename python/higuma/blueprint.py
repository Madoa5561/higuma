from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .exceptions import _validate_error_handler_key
from .parameters import cache_parameter_hints


@dataclass
class _DeferredRoute:
    rule: str
    methods: tuple[str, ...]
    endpoint: str
    view_func: Callable[..., Any]
    options: dict[str, Any]


@dataclass
class _DeferredWebSocket:
    rule: str
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
        template_folder: str | None = None,
        static_folder: str | None = None,
        static_url_path: str = "/static",
        root_path: str | Path | None = None,
    ) -> None:
        if not isinstance(name, str) or not name or "." in name:
            raise ValueError("blueprint names must be nonempty and cannot contain a dot")
        self.name = name
        self.import_name = import_name
        self.url_prefix = url_prefix.rstrip("/")
        from .app import _find_root_path

        self.root_path = Path(root_path).resolve() if root_path else _find_root_path(import_name)
        self.template_folder = (
            str((self.root_path / template_folder).resolve()) if template_folder else None
        )
        self.static_folder = (
            str((self.root_path / static_folder).resolve()) if static_folder else None
        )
        self.static_url_path = static_url_path
        self._children: list[tuple[Blueprint, dict[str, Any]]] = []
        from .commands import CommandGroup

        self.cli = CommandGroup()
        self._routes: list[_DeferredRoute] = []
        self._websockets: list[_DeferredWebSocket] = []
        self._before_request: list[Callable[..., Any]] = []
        self._after_request: list[Callable[..., Any]] = []
        self._teardown_request: list[Callable[..., Any]] = []
        self._context_processors: list[Callable[[], Mapping[str, Any]]] = []
        self._error_handlers: dict[int | type[BaseException], Callable[..., Any]] = {}
        self._url_value_preprocessors: list[Callable[..., Any]] = []
        self._url_defaults: list[Callable[..., Any]] = []
        self._app_before_request: list[Callable[..., Any]] = []
        self._app_after_request: list[Callable[..., Any]] = []
        self._app_teardown_request: list[Callable[..., Any]] = []
        self._app_context_processors: list[Callable[..., Any]] = []
        self._app_url_value_preprocessors: list[Callable[..., Any]] = []
        self._app_url_defaults: list[Callable[..., Any]] = []
        self._app_error_handlers: dict[int | type[BaseException], Callable[..., Any]] = {}

    def before_app_request(self, func: Callable[..., Any]) -> Callable[..., Any]:
        self._app_before_request.append(func)
        return func

    def after_app_request(self, func: Callable[..., Any]) -> Callable[..., Any]:
        self._app_after_request.append(func)
        return func

    def teardown_app_request(self, func: Callable[..., Any]) -> Callable[..., Any]:
        self._app_teardown_request.append(func)
        return func

    def app_context_processor(self, func: Callable[..., Any]) -> Callable[..., Any]:
        self._app_context_processors.append(func)
        return func

    def url_value_preprocessor(self, func: Callable[..., Any]) -> Callable[..., Any]:
        self._url_value_preprocessors.append(func)
        return func

    def url_defaults(self, func: Callable[..., Any]) -> Callable[..., Any]:
        self._url_defaults.append(func)
        return func

    def app_url_value_preprocessor(self, func: Callable[..., Any]) -> Callable[..., Any]:
        self._app_url_value_preprocessors.append(func)
        return func

    def app_url_defaults(self, func: Callable[..., Any]) -> Callable[..., Any]:
        self._app_url_defaults.append(func)
        return func

    def app_errorhandler(self, code_or_exception: int | type[BaseException]):
        key = _validate_error_handler_key(code_or_exception)

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if not callable(func):
                raise TypeError("error handler must be callable")
            self._app_error_handlers[key] = func
            return func

        return decorator

    def register_blueprint(
        self, blueprint: Blueprint, *, url_prefix: str | None = None, name: str | None = None
    ) -> None:
        if not isinstance(blueprint, Blueprint):
            raise TypeError("register_blueprint requires a Blueprint instance")
        if blueprint is self:
            raise ValueError("a blueprint cannot contain itself")
        if name is not None and (not isinstance(name, str) or not name or "." in name):
            raise ValueError(
                "blueprint registration names must be nonempty and cannot contain a dot"
            )
        self._children.append((blueprint, {"url_prefix": url_prefix, "name": name}))

    def before_request(self, func: Callable[..., Any]) -> Callable[..., Any]:
        self._before_request.append(func)
        return func

    def after_request(self, func: Callable[..., Any]) -> Callable[..., Any]:
        self._after_request.append(func)
        return func

    def teardown_request(self, func: Callable[..., Any]) -> Callable[..., Any]:
        self._teardown_request.append(func)
        return func

    def context_processor(
        self, func: Callable[[], Mapping[str, Any]]
    ) -> Callable[[], Mapping[str, Any]]:
        self._context_processors.append(func)
        return func

    def register_error_handler(
        self, code_or_exception: int | type[BaseException], handler: Callable[..., Any]
    ) -> None:
        key = _validate_error_handler_key(code_or_exception)
        if not callable(handler):
            raise TypeError("error handler must be callable")
        self._error_handlers[key] = handler

    def errorhandler(self, code_or_exception: int | type[BaseException]):
        key = _validate_error_handler_key(code_or_exception)

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.register_error_handler(key, func)
            return func

        return decorator

    def route(
        self,
        rule: str,
        *,
        methods: Iterable[str] | None = None,
        endpoint: str | None = None,
        **options: Any,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        if isinstance(methods, (str, bytes, bytearray)):
            raise TypeError(
                "methods must be an iterable of HTTP method strings, not a single string"
            )
        if isinstance(options.get("tags"), (str, bytes, bytearray)):
            raise TypeError("tags must be an iterable of tag strings, not a single string")
        explicit_methods = (
            tuple(dict.fromkeys(method.upper() for method in methods))
            if methods is not None
            else None
        )
        if explicit_methods is not None and not explicit_methods:
            raise ValueError("methods must not be empty")

        def decorator(view_func: Callable[..., Any]) -> Callable[..., Any]:
            import inspect

            if "." in (endpoint or view_func.__name__):
                raise ValueError("blueprint endpoints cannot contain a dot")
            if explicit_methods is None:
                view_methods = getattr(view_func, "methods", None) or ("GET",)
                if isinstance(view_methods, (str, bytes, bytearray)):
                    raise TypeError(
                        "methods must be an iterable of HTTP method strings, not a single string"
                    )
                method_tuple = tuple(dict.fromkeys(method.upper() for method in view_methods))
            else:
                method_tuple = explicit_methods
            frame = inspect.currentframe()
            try:
                cache_parameter_hints(
                    view_func,
                    frame.f_back.f_locals if frame is not None and frame.f_back is not None else {},
                    extra_models=(options.get("request_body"), options.get("response_model")),
                )
            finally:
                del frame
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

    def websocket(
        self,
        rule: str,
        *,
        endpoint: str | None = None,
        **options: Any,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        if isinstance(options.get("allowed_origins"), (str, bytes, bytearray)):
            raise TypeError(
                "allowed_origins must be an iterable of origin strings, not a single string"
            )

        def decorator(view_func: Callable[..., Any]) -> Callable[..., Any]:
            if "." in (endpoint or view_func.__name__):
                raise ValueError("blueprint endpoints cannot contain a dot")
            self._websockets.append(
                _DeferredWebSocket(
                    rule=rule,
                    endpoint=endpoint or view_func.__name__,
                    view_func=view_func,
                    options=options,
                )
            )
            return view_func

        return decorator
