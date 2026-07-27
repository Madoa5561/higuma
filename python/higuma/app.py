from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import traceback
from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar, Token
from functools import wraps
from html import escape
from pathlib import Path
from threading import Thread
from typing import Any

from ._core import HigumaCore
from .blueprint import Blueprint
from .config import Config
from .exceptions import HTTPException, MethodNotAllowed, NotFound
from .request import LocalProxy, Request, _pop_request, _push_request
from .response import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    ResponseValue,
    TemplateResponse,
    make_response,
)
from .routing import Rule, normalize_rule

__version__ = "0.1.0"

ErrorHandler = Callable[..., ResponseValue]
Middleware = Callable[[Request, Callable[[Request], ResponseValue]], ResponseValue]

_app_context: ContextVar[Higuma | None] = ContextVar("higuma_app", default=None)


class _AppProxy(LocalProxy["Higuma"]):
    def _get_current(self) -> Higuma:
        value = _app_context.get()
        if value is None:
            raise RuntimeError("working outside of an application context")
        return value


current_app: LocalProxy[Higuma] = _AppProxy()


class Higuma:
    def __init__(
        self,
        import_name: str,
        *,
        template_folder: str = "templates",
        static_folder: str | None = "static",
        static_url_path: str = "/static",
        max_content_length: int = 8 * 1024 * 1024,
        debug: bool = False,
    ) -> None:
        self.import_name = import_name
        self.root_path = _find_root_path(import_name)
        self.template_folder = _resolve_folder(self.root_path, template_folder)
        self.static_folder = (
            _resolve_folder(self.root_path, static_folder)
            if static_folder
            else None
        )
        self.static_url_path = normalize_rule(static_url_path)
        self.debug = debug
        self.logger = logging.getLogger(import_name)
        self.config = Config(
            DEBUG=debug,
            TESTING=False,
            MAX_CONTENT_LENGTH=max_content_length,
            STATIC_CACHE_MAX_AGE=3600,
            SERVER_HEADER=f"higuma/{__version__}",
        )

        self._routes: list[Rule] = []
        self._endpoint_rules: dict[str, Rule] = {}
        self._before_request: list[Callable[..., ResponseValue | None]] = []
        self._after_request: list[Callable[..., ResponseValue]] = []
        self._middlewares: list[Middleware] = []
        self._error_handlers: dict[int | type[BaseException], ErrorHandler] = {}
        self._startup_handlers: list[Callable[..., Any]] = []
        self._shutdown_handlers: list[Callable[..., Any]] = []
        self._context_processors: list[Callable[[], Mapping[str, Any]]] = []

        self._core = HigumaCore(
            self.template_folder,
            max_content_length,
            self.config["SERVER_HEADER"],
        )
        self._core.set_fallback(self._fallback_callback)

        if self.static_folder:
            self.add_url_rule(
                f"{self.static_url_path}/<path:filename>",
                endpoint="static",
                view_func=self._serve_static,
                methods=("GET",),
            )

    def route(
        self,
        rule: str,
        *,
        methods: Iterable[str] | None = None,
        endpoint: str | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(view_func: Callable[..., Any]) -> Callable[..., Any]:
            self.add_url_rule(
                rule,
                endpoint=endpoint or view_func.__name__,
                view_func=view_func,
                methods=methods or ("GET",),
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

    def add_url_rule(
        self,
        rule: str,
        *,
        endpoint: str,
        view_func: Callable[..., Any],
        methods: Iterable[str] = ("GET",),
    ) -> None:
        method_tuple = tuple(dict.fromkeys(method.upper() for method in methods))
        if not method_tuple:
            raise ValueError("methods must not be empty")
        if endpoint in self._endpoint_rules:
            raise ValueError(f"endpoint {endpoint!r} is already registered")

        route = Rule(
            rule=rule,
            methods=method_tuple,
            endpoint=endpoint,
            view_func=view_func,
        )

        @wraps(view_func)
        def callback(raw_request: Mapping[str, Any]) -> Any:
            return self._dispatch_route(route, raw_request)

        route.callback = callback
        self._routes.append(route)
        self._routes.sort(key=lambda item: item.specificity, reverse=True)
        self._endpoint_rules[endpoint] = route
        self._core.add_route(route.rule, list(method_tuple), callback)

    def register_blueprint(
        self,
        blueprint: Blueprint,
        *,
        url_prefix: str | None = None,
        name_prefix: str = "",
    ) -> None:
        prefix = (url_prefix if url_prefix is not None else blueprint.url_prefix).rstrip(
            "/"
        )
        for deferred in blueprint._routes:
            full_rule = f"{prefix}{deferred.rule}"
            endpoint = f"{name_prefix}{blueprint.name}.{deferred.endpoint}"
            self.add_url_rule(
                full_rule,
                endpoint=endpoint,
                view_func=deferred.view_func,
                methods=deferred.methods,
            )

    def before_request(self, func: Callable[..., ResponseValue | None]):
        self._before_request.append(func)
        return func

    def after_request(self, func: Callable[..., ResponseValue]):
        self._after_request.append(func)
        return func

    def middleware(self, func: Middleware) -> Middleware:
        self._middlewares.append(func)
        return func

    def add_middleware(
        self,
        middleware: Middleware | type,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        instance = middleware(*args, **kwargs) if isinstance(middleware, type) else middleware
        self._middlewares.append(instance)
        return instance

    def errorhandler(
        self,
        code_or_exception: int | type[BaseException],
    ) -> Callable[[ErrorHandler], ErrorHandler]:
        def decorator(func: ErrorHandler) -> ErrorHandler:
            self._error_handlers[code_or_exception] = func
            return func

        return decorator

    def context_processor(
        self, func: Callable[[], Mapping[str, Any]]
    ) -> Callable[[], Mapping[str, Any]]:
        self._context_processors.append(func)
        return func

    def on_startup(self, func: Callable[..., Any]) -> Callable[..., Any]:
        self._startup_handlers.append(func)
        return func

    def on_shutdown(self, func: Callable[..., Any]) -> Callable[..., Any]:
        self._shutdown_handlers.append(func)
        return func

    def render_template(
        self,
        template: str,
        /,
        *,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
        **context: Any,
    ) -> TemplateResponse:
        merged_context: dict[str, Any] = {}
        for processor in self._context_processors:
            merged_context.update(processor())
        merged_context.update(context)
        return TemplateResponse(
            template=template,
            context=merged_context,
            status=status,
            headers=dict(headers or {}),
        )

    def jsonify(
        self,
        data: Any = None,
        /,
        *,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
        **fields: Any,
    ) -> JSONResponse:
        if data is not None and fields:
            raise TypeError("jsonify accepts either one positional value or keyword fields")
        return JSONResponse(fields if data is None else data, status, headers)

    def make_response(
        self,
        value: ResponseValue,
        status: int | None = None,
        headers: Mapping[str, str] | None = None,
    ):
        return make_response(value, status, headers)

    def url_for(self, endpoint: str, **values: Any) -> str:
        try:
            rule = self._endpoint_rules[endpoint]
        except KeyError as exc:
            raise KeyError(f"unknown endpoint: {endpoint}") from exc
        return rule.build(values)

    def test_client(self):
        from .testing import TestClient

        return TestClient(self)

    def run(
        self,
        host: str = "127.0.0.1",
        port: int = 8000,
        *,
        workers: int = 0,
        debug: bool | None = None,
    ) -> None:
        if debug is not None:
            self.debug = debug
            self.config["DEBUG"] = debug
        for handler in self._startup_handlers:
            _resolve_awaitable(handler())
        try:
            self._core.run(host, port, workers)
        finally:
            for handler in reversed(self._shutdown_handlers):
                _resolve_awaitable(handler())

    def _dispatch_route(
        self,
        route: Rule,
        raw_request: Mapping[str, Any],
    ) -> Any:
        request = Request(raw_request)
        request.path_params = route.convert_params(request.path_params)
        request.view_args = request.path_params
        return self._dispatch(request, lambda: self._invoke_view(route, request))

    def _fallback_callback(self, raw_request: Mapping[str, Any]) -> Any:
        request = Request(raw_request)

        def fallback() -> Any:
            if request.route_error_status == 405:
                raise MethodNotAllowed(
                    headers={"allow": ", ".join(request.allowed_methods)}
                )
            raise NotFound()

        return self._dispatch(request, fallback)

    def _dispatch(self, request: Request, endpoint_call: Callable[[], Any]) -> Any:
        request_token = _push_request(request)
        app_token: Token[Higuma | None] = _app_context.set(self)

        def terminal(current_request: Request) -> Any:
            try:
                for hook in self._before_request:
                    result = _call_hook(hook, current_request)
                    if result is not None:
                        return result
                return endpoint_call()
            except Exception as exc:  # noqa: BLE001 - framework exception boundary
                return self._handle_exception(exc, current_request)

        call_next: Callable[[Request], Any] = terminal
        for item in reversed(self._middlewares):
            next_handler = call_next

            def call_middleware(
                current_request: Request,
                middleware: Middleware = item,
                next_call: Callable[[Request], Any] = next_handler,
            ) -> Any:
                return _resolve_awaitable(middleware(current_request, next_call))

            call_next = call_middleware

        try:
            try:
                response = make_response(call_next(request))
            except Exception as exc:  # noqa: BLE001 - middleware exception boundary
                response = make_response(self._handle_exception(exc, request))

            for hook in reversed(self._after_request):
                response = make_response(_call_after_hook(hook, request, response))
            return response
        finally:
            _app_context.reset(app_token)
            _pop_request(request_token)

    def _invoke_view(self, route: Rule, request: Request) -> Any:
        signature = inspect.signature(route.view_func)
        params = signature.parameters
        args: list[Any] = []
        kwargs: dict[str, Any] = {}
        accepts_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in params.values()
        )

        for name, value in request.path_params.items():
            if name in params or accepts_kwargs:
                kwargs[name] = value

        if "request" in params:
            kwargs["request"] = request
        else:
            missing_positional = [
                parameter
                for parameter in params.values()
                if parameter.kind
                in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                )
                and parameter.default is inspect.Parameter.empty
                and parameter.name not in kwargs
            ]
            if missing_positional:
                args.append(request)

        return _resolve_awaitable(route.view_func(*args, **kwargs))

    def _handle_exception(self, error: BaseException, request: Request) -> ResponseValue:
        handler = self._find_error_handler(error)
        if handler is not None:
            try:
                return _call_error_handler(handler, error, request)
            except BaseException:
                self.logger.exception("error handler failed")

        if isinstance(error, HTTPException):
            return self._default_http_error(error)

        self.logger.exception("unhandled request exception", exc_info=error)
        if self.debug:
            detail = traceback.format_exc()
            return HTMLResponse(
                "<h1>500 Internal Server Error</h1>"
                f"<pre>{escape(detail)}</pre>",
                500,
            )
        return self._default_http_error(
            HTTPException(500, "Internal Server Error")
        )

    def _find_error_handler(self, error: BaseException) -> ErrorHandler | None:
        if isinstance(error, HTTPException):
            handler = self._error_handlers.get(error.status_code)
            if handler is not None:
                return handler
        for error_type in type(error).__mro__:
            handler = self._error_handlers.get(error_type)
            if handler is not None:
                return handler
        return None

    @staticmethod
    def _default_http_error(error: HTTPException) -> HTMLResponse:
        body = (
            "<!doctype html><html><head><meta charset=\"utf-8\">"
            f"<title>{error.status_code} {escape(error.detail)}</title></head>"
            "<body><main>"
            f"<h1>{error.status_code}</h1><p>{escape(error.detail)}</p>"
            "</main></body></html>"
        )
        return HTMLResponse(body, error.status_code, error.headers)

    def _serve_static(self, request: Request, filename: str) -> ResponseValue:
        if not self.static_folder:
            raise NotFound()
        root = Path(self.static_folder).resolve()
        candidate = (root / filename).resolve()
        if root != candidate and root not in candidate.parents:
            raise NotFound()
        if not candidate.is_file():
            raise NotFound()

        stat = candidate.stat()
        etag = f'"{hashlib.sha256(f"{stat.st_mtime_ns}:{stat.st_size}".encode()).hexdigest()[:24]}"'
        cache_control = f"public, max-age={int(self.config['STATIC_CACHE_MAX_AGE'])}"
        common_headers = {"etag": etag, "cache-control": cache_control}
        if request.headers.get("if-none-match") == etag:
            return Response(b"", 304, common_headers)

        range_header = request.headers.get("range")
        if range_header and str(range_header).startswith("bytes="):
            parsed_range = _parse_range(str(range_header), stat.st_size)
            if parsed_range is not None:
                start, end = parsed_range
                with candidate.open("rb") as handle:
                    handle.seek(start)
                    body = handle.read(end - start + 1)
                return Response(
                    body,
                    206,
                    {
                        **common_headers,
                        "accept-ranges": "bytes",
                        "content-range": f"bytes {start}-{end}/{stat.st_size}",
                    },
                    _guess_media_type(candidate),
                )

        return FileResponse(
            candidate,
            headers={**common_headers, "accept-ranges": "bytes"},
        )

    def _match_route(
        self, path: str, method: str
    ) -> tuple[Rule | None, dict[str, Any], tuple[str, ...]]:
        method = method.upper()
        allowed: set[str] = set()
        matched: tuple[int, Rule, dict[str, Any]] | None = None

        for route in self._routes:
            params = route.match(path)
            if params is None:
                continue
            allowed.update(route.methods)
            if "GET" in route.methods:
                allowed.add("HEAD")
            priority = (
                3
                if method in route.methods
                else 2
                if method == "HEAD" and "GET" in route.methods
                else 0
            )
            if priority and (matched is None or priority > matched[0]):
                matched = (priority, route, params)

        if allowed:
            allowed.add("OPTIONS")
        methods = tuple(sorted(allowed))
        if matched:
            return matched[1], matched[2], methods
        return None, {}, methods


def _find_root_path(import_name: str) -> Path:
    try:
        module = __import__(import_name, fromlist=["__file__"])
        module_file = getattr(module, "__file__", None)
        if module_file:
            return Path(module_file).resolve().parent
    except (ImportError, TypeError):
        pass
    return Path.cwd()


def _resolve_folder(root: Path, folder: str | None) -> str:
    if folder is None:
        return ""
    path = Path(folder)
    if path.is_absolute():
        return str(path)
    return str((root / path).resolve())


def _resolve_awaitable(value: Any) -> Any:
    if not inspect.isawaitable(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)

    result: list[Any] = []
    errors: list[Exception] = []

    def runner() -> None:
        try:
            result.append(asyncio.run(value))
        except Exception as exc:  # noqa: BLE001 - propagate async task failures
            errors.append(exc)

    thread = Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    return result[0]


def _call_hook(func: Callable[..., Any], request: Request) -> Any:
    return _resolve_awaitable(func(request) if inspect.signature(func).parameters else func())


def _call_after_hook(func: Callable[..., Any], request: Request, response: Any) -> Any:
    count = len(inspect.signature(func).parameters)
    if count >= 2:
        return _resolve_awaitable(func(request, response))
    return _resolve_awaitable(func(response))


def _call_error_handler(
    func: ErrorHandler, error: BaseException, request: Request
) -> Any:
    count = len(inspect.signature(func).parameters)
    if count >= 2:
        return _resolve_awaitable(func(error, request))
    return _resolve_awaitable(func(error))


def _parse_range(value: str, size: int) -> tuple[int, int] | None:
    try:
        spec = value.removeprefix("bytes=").split(",", 1)[0]
        start_text, end_text = spec.split("-", 1)
        if not start_text:
            length = int(end_text)
            if length <= 0:
                return None
            return max(0, size - length), size - 1
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
        if start < 0 or start >= size or end < start:
            return None
        return start, min(end, size - 1)
    except (ValueError, ZeroDivisionError):
        return None


def _guess_media_type(path: Path) -> str:
    import mimetypes

    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"
