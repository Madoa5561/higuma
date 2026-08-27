from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import traceback
from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar, Token, copy_context
from functools import wraps
from html import escape
from pathlib import Path
from threading import Event, Thread
from types import TracebackType
from typing import Any

from ._core import HigumaCore
from .background import BackgroundTask, BackgroundTasks
from .blueprint import Blueprint
from .config import Config
from .exceptions import HTTPException, MethodNotAllowed, NotFound
from .mounts import asgi_view, wsgi_view
from .openapi import generate_openapi, swagger_ui_html
from .parameters import (
    RequestValidationError,
    cache_parameter_hints,
    coerce_response_model,
    resolve_arguments,
)
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
from .websocket import WebSocket

__version__ = "0.4.0"

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
        openapi_url: str | None = "/openapi.json",
        docs_url: str | None = "/docs",
        lifespan: Callable[[Higuma], Any] | None = None,
    ) -> None:
        if max_content_length <= 0:
            raise ValueError("max_content_length must be positive")
        self.import_name = import_name
        self.root_path = _find_root_path(import_name)
        self.template_folder = _resolve_folder(self.root_path, template_folder)
        self.static_folder = (
            _resolve_folder(self.root_path, static_folder) if static_folder else None
        )
        self.static_url_path = normalize_rule(static_url_path)
        self.debug = debug
        self.logger = logging.getLogger(import_name)
        self.state: dict[str, Any] = {}
        self._lifespan_handler = lifespan
        self.config = Config(
            DEBUG=debug,
            TESTING=False,
            MAX_CONTENT_LENGTH=max_content_length,
            STATIC_CACHE_MAX_AGE=3600,
            SERVER_HEADER=f"higuma/{__version__}",
            OPENAPI_TITLE=import_name,
            OPENAPI_VERSION=__version__,
            OPENAPI_DESCRIPTION="",
        )

        self._routes: list[Rule] = []
        self._endpoint_rules: dict[str, Rule] = {}
        self._websocket_rules: dict[str, Rule] = {}
        self._before_request: list[Callable[..., ResponseValue | None]] = []
        self._after_request: list[Callable[..., ResponseValue]] = []
        self._middlewares: list[Middleware] = []
        self._error_handlers: dict[int | type[BaseException], ErrorHandler] = {}
        self._startup_handlers: list[Callable[..., Any]] = []
        self._shutdown_handlers: list[Callable[..., Any]] = []
        self._context_processors: list[Callable[[], Mapping[str, Any]]] = []
        self.dependency_overrides: dict[Callable[..., Any], Callable[..., Any]] = {}

        self._core = HigumaCore(
            self.template_folder,
            max_content_length,
            self.config["SERVER_HEADER"],
        )
        self._core.set_fallback(self._fallback_callback)

        if openapi_url:
            self.add_url_rule(
                openapi_url,
                endpoint="openapi",
                view_func=self.openapi,
                methods=("GET",),
                include_in_schema=False,
            )
        if docs_url and openapi_url:

            def openapi_docs() -> HTMLResponse:
                return HTMLResponse(
                    swagger_ui_html(
                        normalize_rule(openapi_url),
                        f"{self.config['OPENAPI_TITLE']} API",
                    ),
                    headers={
                        "content-security-policy": (
                            "default-src 'self'; "
                            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net"
                        )
                    },
                )

            self.add_url_rule(
                docs_url,
                endpoint="openapi_docs",
                view_func=openapi_docs,
                methods=("GET",),
                include_in_schema=False,
            )

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
        summary: str | None = None,
        description: str | None = None,
        tags: Iterable[str] | None = None,
        responses: Mapping[str, Any] | None = None,
        request_body: Any = None,
        response_model: Any = None,
        status_code: int | None = None,
        operation_id: str | None = None,
        include_in_schema: bool = True,
        openapi_extra: Mapping[str, Any] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(view_func: Callable[..., Any]) -> Callable[..., Any]:
            frame = inspect.currentframe()
            try:
                cache_parameter_hints(
                    view_func,
                    frame.f_back.f_locals if frame is not None and frame.f_back is not None else {},
                )
            finally:
                del frame
            self.add_url_rule(
                rule,
                endpoint=endpoint or view_func.__name__,
                view_func=view_func,
                methods=methods or ("GET",),
                summary=summary,
                description=description,
                tags=tags,
                responses=responses,
                request_body=request_body,
                response_model=response_model,
                status_code=status_code,
                operation_id=operation_id,
                include_in_schema=include_in_schema,
                openapi_extra=openapi_extra,
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
        methods: Iterable[str] | None = None,
        summary: str | None = None,
        description: str | None = None,
        tags: Iterable[str] | None = None,
        responses: Mapping[str, Any] | None = None,
        request_body: Any = None,
        response_model: Any = None,
        status_code: int | None = None,
        operation_id: str | None = None,
        include_in_schema: bool = True,
        openapi_extra: Mapping[str, Any] | None = None,
    ) -> None:
        declared_methods = methods or getattr(view_func, "methods", None) or ("GET",)
        method_tuple = tuple(dict.fromkeys(method.upper() for method in declared_methods))
        if not method_tuple:
            raise ValueError("methods must not be empty")
        if endpoint in self._endpoint_rules:
            raise ValueError(f"endpoint {endpoint!r} is already registered")
        if status_code is not None:
            Response(b"", status_code)
        normalized_rule = normalize_rule(rule)
        for existing in self._routes:
            duplicate_methods = set(existing.methods) & set(method_tuple)
            if existing.rule == normalized_rule and duplicate_methods:
                methods_text = ", ".join(sorted(duplicate_methods))
                raise ValueError(f"duplicate route registration: {methods_text} {normalized_rule}")

        route = Rule(
            rule=rule,
            methods=method_tuple,
            endpoint=endpoint,
            view_func=view_func,
            openapi={
                key: value
                for key, value in {
                    "summary": summary,
                    "description": description,
                    "tags": tuple(tags) if tags else None,
                    "responses": dict(responses) if responses else None,
                    "request_body": request_body,
                    "response_model": response_model,
                    "status_code": status_code,
                    "operation_id": operation_id,
                    "openapi_extra": dict(openapi_extra or {}),
                }.items()
                if value is not None
            },
            include_in_schema=include_in_schema,
        )

        @wraps(view_func)
        def callback(raw_request: Mapping[str, Any]) -> Any:
            return self._dispatch_route(route, raw_request)

        route.callback = callback
        self._core.add_route(route.rule, list(method_tuple), callback)
        self._routes.append(route)
        self._routes.sort(key=lambda item: item.specificity, reverse=True)
        self._endpoint_rules[endpoint] = route

    def websocket(
        self,
        rule: str,
        *,
        endpoint: str | None = None,
        allowed_origins: Iterable[str] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(handler: Callable[..., Any]) -> Callable[..., Any]:
            route_endpoint = endpoint or handler.__name__
            if route_endpoint in self._websocket_rules:
                raise ValueError(f"WebSocket endpoint {route_endpoint!r} is already registered")
            normalized_rule = normalize_rule(rule)
            if any(item.rule == normalized_rule for item in self._websocket_rules.values()):
                raise ValueError(f"duplicate WebSocket route registration: {normalized_rule}")
            route = Rule(
                rule=rule,
                methods=("WEBSOCKET",),
                endpoint=route_endpoint,
                view_func=handler,
                include_in_schema=False,
            )

            @wraps(handler)
            def callback(raw_request: Mapping[str, Any], session: Any) -> Any:
                return self._dispatch_websocket(route, raw_request, session)

            @wraps(handler)
            def preflight(raw_request: Mapping[str, Any]) -> Any:
                return self._dispatch_websocket_preflight(route, raw_request)

            route.callback = callback
            self._core.add_websocket_route(
                route.rule,
                callback,
                preflight,
                list(allowed_origins or ()),
            )
            self._websocket_rules[route_endpoint] = route
            return handler

        return decorator

    def register_blueprint(
        self,
        blueprint: Blueprint,
        *,
        url_prefix: str | None = None,
        name_prefix: str = "",
    ) -> None:
        prefix = (url_prefix if url_prefix is not None else blueprint.url_prefix).rstrip("/")
        for deferred in blueprint._routes:
            full_rule = f"{prefix}{deferred.rule}"
            endpoint = f"{name_prefix}{blueprint.name}.{deferred.endpoint}"
            self.add_url_rule(
                full_rule,
                endpoint=endpoint,
                view_func=deferred.view_func,
                methods=deferred.methods,
                **deferred.options,
            )
        for deferred in blueprint._websockets:
            full_rule = f"{prefix}{deferred.rule}"
            endpoint = f"{name_prefix}{blueprint.name}.{deferred.endpoint}"
            self.websocket(
                full_rule,
                endpoint=endpoint,
                **deferred.options,
            )(deferred.view_func)

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

    @contextmanager
    def lifespan(self):
        manager: Any = nullcontext(None)
        if self._lifespan_handler is not None:
            context = self._lifespan_handler(self)
            if hasattr(context, "__aenter__") and hasattr(context, "__aexit__"):
                manager = _AsyncContextManagerAdapter(context)
            elif hasattr(context, "__enter__") and hasattr(context, "__exit__"):
                manager = context
            else:
                raise TypeError("lifespan must return a sync or async context manager")

        with manager as lifespan_state:
            if lifespan_state is not None:
                if not isinstance(lifespan_state, Mapping):
                    raise TypeError("lifespan must yield a mapping or None")
                self.state.update(lifespan_state)
            for handler in self._startup_handlers:
                _resolve_awaitable(
                    handler(self) if inspect.signature(handler).parameters else handler()
                )
            try:
                yield self
            finally:
                for handler in reversed(self._shutdown_handlers):
                    _resolve_awaitable(
                        handler(self) if inspect.signature(handler).parameters else handler()
                    )

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

    def openapi(self) -> dict[str, Any]:
        return generate_openapi(self)

    def clear_template_cache(self) -> None:
        self._core.clear_template_cache()

    def init_database(self, url: str = "sqlite:///higuma.db"):
        from .database import Database

        self.database = Database(url)
        return self.database

    def mount_wsgi(
        self,
        prefix: str,
        application: Callable[..., Any],
        *,
        name: str = "wsgi",
    ) -> None:
        self._mount_application(prefix, wsgi_view(application, normalize_rule(prefix)), name)

    def mount_asgi(
        self,
        prefix: str,
        application: Callable[..., Any],
        *,
        name: str = "asgi",
    ) -> None:
        self._mount_application(prefix, asgi_view(application, normalize_rule(prefix)), name)

    def _mount_application(
        self,
        prefix: str,
        view_func: Callable[..., Any],
        name: str,
    ) -> None:
        normalized = normalize_rule(prefix)
        methods = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
        self.add_url_rule(
            normalized,
            endpoint=f"{name}_root",
            view_func=view_func,
            methods=methods,
            include_in_schema=False,
        )
        self.add_url_rule(
            f"{normalized}/<path:mounted_path>",
            endpoint=f"{name}_path",
            view_func=view_func,
            methods=methods,
            include_in_schema=False,
        )

    def run(
        self,
        host: str = "127.0.0.1",
        port: int = 8000,
        *,
        workers: int = 0,
        processes: int = 1,
        app_ref: str | None = None,
        debug: bool | None = None,
        max_connections: int = 1024,
        max_restarts: int = 5,
        restart_window: float = 60.0,
    ) -> None:
        if workers < 0 or processes < 1:
            raise ValueError("workers must be non-negative and processes must be positive")
        if debug is not None:
            self.debug = debug
            self.config["DEBUG"] = debug
        if processes > 1:
            if not app_ref:
                raise ValueError(
                    "app_ref='module:app' is required when processes is greater than one"
                )
            from .supervisor import Supervisor

            Supervisor(
                app_ref,
                host=host,
                port=port,
                processes=processes,
                threads=workers,
                max_connections=max_connections,
                max_restarts=max_restarts,
                restart_window=restart_window,
            ).run()
            return
        with self.lifespan():
            self._core.run(host, port, workers)

    def _dispatch_websocket(
        self,
        route: Rule,
        raw_request: Mapping[str, Any],
        session: Any,
    ) -> Any:
        request = Request(raw_request)
        request.path_params = route.convert_params(request.path_params)
        request.view_args = request.path_params

        def endpoint_call() -> Any:
            websocket = WebSocket(session)
            signature = inspect.signature(route.view_func)
            kwargs = {
                name: value
                for name, value in request.path_params.items()
                if name in signature.parameters
            }
            parameters = signature.parameters
            if "websocket" in parameters:
                kwargs["websocket"] = websocket
                return _resolve_awaitable(route.view_func(**kwargs))
            if "ws" in parameters:
                kwargs["ws"] = websocket
                return _resolve_awaitable(route.view_func(**kwargs))
            return _resolve_awaitable(route.view_func(websocket, **kwargs))

        return self._dispatch(request, endpoint_call)

    def _dispatch_websocket_preflight(
        self,
        route: Rule,
        raw_request: Mapping[str, Any],
    ) -> Any:
        request = Request(raw_request)
        request.path_params = route.convert_params(request.path_params)
        request.view_args = request.path_params

        def authorize() -> Response:
            from .auth import _run_auth_checks

            _run_auth_checks(route.view_func)
            return Response(b"", 204)

        return self._dispatch(request, authorize, run_hooks=False)

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
            if request.method == "OPTIONS" and request.allowed_methods:
                return Response(
                    b"",
                    204,
                    {"allow": ", ".join(request.allowed_methods)},
                )
            if request.route_error_status == 405:
                raise MethodNotAllowed(headers={"allow": ", ".join(request.allowed_methods)})
            raise NotFound()

        return self._dispatch(request, fallback)

    def _dispatch(
        self,
        request: Request,
        endpoint_call: Callable[[], Any],
        *,
        run_hooks: bool = True,
    ) -> Any:
        request.state["_higuma_dependency_overrides"] = self.dependency_overrides
        request_token = _push_request(request)
        app_token: Token[Higuma | None] = _app_context.set(self)

        def terminal(current_request: Request) -> Any:
            try:
                if run_hooks:
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

            if run_hooks:
                try:
                    for hook in reversed(self._after_request):
                        response = make_response(_call_after_hook(hook, request, response))
                except Exception as exc:  # noqa: BLE001 - after hook exception boundary
                    response = make_response(self._handle_exception(exc, request))
            response = self._prepare_file_response(request, response)
            background_tasks = request.state.get("_higuma_background_tasks")
            if isinstance(background_tasks, BackgroundTasks) and background_tasks:
                if response.background is None:
                    response.background = background_tasks
                elif response.background is not background_tasks:
                    combined = BackgroundTasks([BackgroundTask(response.background)])
                    combined.tasks.extend(background_tasks.tasks)
                    response.background = combined
            dependency_cleanups = request.state.pop("_higuma_dependency_cleanups", [])
            if dependency_cleanups:
                response.background = BackgroundTask(
                    self._finish_response_work,
                    response.background,
                    dependency_cleanups,
                )
            return response
        finally:
            self._run_dependency_cleanups(request.state.pop("_higuma_dependency_cleanups", []))
            _app_context.reset(app_token)
            _pop_request(request_token)

    def _finish_response_work(
        self,
        background: Callable[[], Any] | None,
        cleanups: list[Callable[[], Any]],
    ) -> None:
        try:
            if background is not None:
                background()
        finally:
            self._run_dependency_cleanups(cleanups)

    def _run_dependency_cleanups(self, cleanups: list[Callable[[], Any]]) -> None:
        for cleanup in reversed(cleanups):
            try:
                _resolve_awaitable(cleanup())
            except BaseException:
                self.logger.exception("dependency cleanup failed")

    def _invoke_view(self, route: Rule, request: Request) -> Any:
        args, kwargs = resolve_arguments(
            route.view_func,
            request,
            request.path_params,
            _resolve_awaitable,
        )
        result = _resolve_awaitable(route.view_func(*args, **kwargs))
        response_model = route.openapi.get("response_model")
        declared_status = route.openapi.get("status_code")
        if response_model is not None:
            if isinstance(result, tuple) and len(result) in (2, 3):
                body, *metadata = result
                if not isinstance(body, Response):
                    result = (coerce_response_model(body, response_model), *metadata)
            elif not isinstance(result, Response):
                result = coerce_response_model(result, response_model)
        if declared_status is not None and not isinstance(result, tuple):
            if isinstance(result, Response):
                result.status_code = declared_status
            else:
                result = (result, declared_status)
        return result

    def _handle_exception(self, error: BaseException, request: Request) -> ResponseValue:
        if isinstance(error, RequestValidationError):
            return JSONResponse({"detail": error.errors}, 422)
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
                f"<h1>500 Internal Server Error</h1><pre>{escape(detail)}</pre>",
                500,
            )
        return self._default_http_error(HTTPException(500, "Internal Server Error"))

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
            '<!doctype html><html><head><meta charset="utf-8">'
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
            if parsed_range is None:
                return Response(
                    b"",
                    416,
                    {
                        **common_headers,
                        "content-range": f"bytes */{stat.st_size}",
                    },
                )
            start, end = parsed_range
            return FileResponse(
                candidate,
                status=206,
                headers={
                    **common_headers,
                    "accept-ranges": "bytes",
                    "content-range": f"bytes {start}-{end}/{stat.st_size}",
                },
                media_type=_guess_media_type(candidate),
                offset=start,
                length=end - start + 1,
            )

        return FileResponse(
            candidate,
            headers={**common_headers, "accept-ranges": "bytes"},
        )

    def _prepare_file_response(self, request: Request, response: Response) -> Response:
        if (
            not isinstance(response, FileResponse)
            or response.status_code != 200
            or request.method not in {"GET", "HEAD"}
        ):
            return response
        path = Path(response.path)
        try:
            stat = path.stat()
        except OSError:
            return response
        if response.offset != 0 or response.length != stat.st_size:
            return response

        etag = f'"{hashlib.sha256(f"{stat.st_mtime_ns}:{stat.st_size}".encode()).hexdigest()[:24]}"'
        response.headers.setdefault("etag", etag)
        response.headers.setdefault("accept-ranges", "bytes")
        if request.headers.get("if-none-match") == response.headers["etag"]:
            headers = dict(response.headers)
            headers.pop("content-length", None)
            conditional = Response(b"", 304, headers, background=response.background)
            conditional._extra_headers.extend(response._extra_headers)
            return conditional

        range_header = request.headers.get("range")
        if not range_header or not str(range_header).startswith("bytes="):
            return response
        parsed_range = _parse_range(str(range_header), stat.st_size)
        if parsed_range is None:
            headers = dict(response.headers)
            headers.pop("content-length", None)
            headers["content-range"] = f"bytes */{stat.st_size}"
            unsatisfied = Response(b"", 416, headers, background=response.background)
            unsatisfied._extra_headers.extend(response._extra_headers)
            return unsatisfied

        start, end = parsed_range
        response.status_code = 206
        response.offset = start
        response.length = end - start + 1
        response.headers["content-range"] = f"bytes {start}-{end}/{stat.st_size}"
        response.headers["content-length"] = str(response.length)
        return response

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
    errors: list[BaseException] = []
    context = copy_context()

    def runner() -> None:
        try:
            result.append(context.run(asyncio.run, value))
        except BaseException as exc:  # noqa: BLE001 - propagate async task failures
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


def _call_error_handler(func: ErrorHandler, error: BaseException, request: Request) -> Any:
    count = len(inspect.signature(func).parameters)
    if count >= 2:
        return _resolve_awaitable(func(error, request))
    return _resolve_awaitable(func(error))


def _parse_range(value: str, size: int) -> tuple[int, int] | None:
    if size <= 0:
        return None
    try:
        spec = value.removeprefix("bytes=")
        if "," in spec:
            return None
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


class _AsyncContextManagerAdapter:
    def __init__(self, context: Any) -> None:
        self.context = context
        self.loop: asyncio.AbstractEventLoop | None = None
        self.ready = Event()
        self.thread = Thread(target=self._run_loop, daemon=True, name="higuma-lifespan")

    def _run_loop(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.ready.set()
        self.loop.run_forever()
        self.loop.run_until_complete(self.loop.shutdown_asyncgens())
        self.loop.close()

    def _submit(self, awaitable: Any) -> Any:
        self.ready.wait()
        if self.loop is None:
            raise RuntimeError("lifespan event loop failed to start")
        return asyncio.run_coroutine_threadsafe(awaitable, self.loop).result()

    def __enter__(self) -> Any:
        self.thread.start()
        try:
            return self._submit(self.context.__aenter__())
        except BaseException:
            self._stop()
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        try:
            return bool(self._submit(self.context.__aexit__(exc_type, exc_value, traceback)))
        finally:
            self._stop()

    def _stop(self) -> None:
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread.is_alive():
            self.thread.join()
