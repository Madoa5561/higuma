"""Conservative ASGI coroutine path. Complex sync lifecycle features use the bridge."""

from __future__ import annotations

import asyncio
import inspect

from .background import BackgroundTask, BackgroundTasks
from .execution import await_bridge
from .parameters import (
    Depends,
    File,
    Form,
    resolve_arguments,
    resolved_parameter_hints,
    split_parameter_annotation,
)
from .request import Request, _pop_request, _push_request
from .response import FileResponse, StreamingResponse, TemplateResponse, make_response
from .signals import has_lifecycle_receivers


def eligible(app, route, storage):
    if not app.config.get("ASGI_ASYNC_FAST_PATH", True) or route is None:
        return False
    if not inspect.iscoroutinefunction(route.view_func) or storage.rolled_to_disk:
        return False
    if has_lifecycle_receivers(app) or app._middlewares or app._teardown_appcontext:
        return False
    scopes = [app, *app._blueprint_chain(route.endpoint.rpartition(".")[0] or None)]
    if any(
        any(
            getattr(item, name)
            for name in (
                "_before_request",
                "_after_request",
                "_teardown_request",
                "_error_handlers",
                "_url_value_preprocessors",
            )
        )
        for item in scopes
    ):
        return False
    hints = resolved_parameter_hints(route.view_func)
    for parameter in inspect.signature(route.view_func).parameters.values():
        base, marker = split_parameter_annotation(
            hints.get(parameter.name, parameter.annotation), parameter.default
        )
        if isinstance(marker, (Depends, File, Form)) or base is BackgroundTasks:
            return False
    return True


async def run(app, route, raw, send, receive, respond, worker):
    from .gateways import _headers

    async def never_disconnect():
        return await asyncio.Future()

    async def call(callback, *args):
        result = (
            callback(*args)
            if inspect.iscoroutinefunction(callback)
            else await asyncio.to_thread(callback, *args)
        )
        return await result if inspect.isawaitable(result) else result

    async def execute():
        request = Request(raw)
        request.path_params = route.convert_params(request.path_params)
        request.view_args = request.path_params
        request.endpoint = route.endpoint
        request.blueprint = route.endpoint.rpartition(".")[0] or None
        request.max_form_memory_size = int(app.config["MAX_FORM_MEMORY_SIZE"])
        request.max_form_parts = int(app.config["MAX_FORM_PARTS"])
        request.upload_memory_limit = int(app.config["UPLOAD_MEMORY_LIMIT"])
        if (
            min(request.max_form_memory_size, request.max_form_parts, request.upload_memory_limit)
            < 1
        ):
            raise ValueError("multipart limits must be positive")
        request.state["_higuma_dispatching"] = True
        request.state["_higuma_async_fast_path"] = True
        with app.app_context():
            token = _push_request(request)
            response = None
            try:
                try:
                    args, kwargs = resolve_arguments(
                        route.view_func, request, request.path_params, lambda value: value
                    )
                    result = await route.view_func(*args, **kwargs)
                    response = make_response(app._coerce_view_result(route, result))
                except Exception as error:  # noqa: BLE001 - framework exception boundary
                    response = make_response(app._handle_exception(error, request))
                try:
                    for callback in request.state.pop("_higuma_after_request", []):
                        response = make_response(await call(callback, response))
                    for callback in request.state.pop("_higuma_response_finalizers", []):
                        response = make_response(await call(callback, response))
                except Exception as error:  # noqa: BLE001 - response finalizer exception boundary
                    response = make_response(app._handle_exception(error, request))
                if (
                    isinstance(response, (FileResponse, StreamingResponse, TemplateResponse))
                    or response.background
                ):
                    prebuilt, response = response, None

                    def serialize():
                        bridge = await_bridge.get()

                        def bind(value):
                            if hasattr(value, "_context"):
                                value._context.run(await_bridge.set, bridge)
                            if isinstance(value, BackgroundTasks):
                                for task in value.tasks:
                                    bind(task)
                            elif isinstance(value, BackgroundTask) and isinstance(
                                value.func, (BackgroundTask, BackgroundTasks)
                            ):
                                bind(value.func)

                        bind(prebuilt)
                        if prebuilt.background is not None:
                            if not isinstance(
                                prebuilt.background, (BackgroundTask, BackgroundTasks)
                            ):
                                prebuilt.background = BackgroundTask(prebuilt.background)
                            bind(prebuilt.background)
                        prepared = app._prepare_file_response(request, prebuilt)
                        respond(prepared)

                    await worker(serialize, never_disconnect)
                else:
                    length = 0 if response.status_code == 205 else len(response.body)
                    try:
                        headers = [
                            (key.encode(), value.encode("latin-1"))
                            for key, value in _headers(response, length)
                        ]
                    except Exception as error:  # noqa: BLE001 - response serialization boundary
                        response = make_response(app._handle_exception(error, request))
                        headers = [
                            (key.encode(), value.encode("latin-1"))
                            for key, value in _headers(response, len(response.body))
                        ]
                    await send(
                        {
                            "type": "http.response.start",
                            "status": response.status_code,
                            "headers": headers,
                        }
                    )
                    suppressed = request.method == "HEAD" or response.status_code in {204, 205, 304}
                    await send(
                        {"type": "http.response.body", "body": b"" if suppressed else response.body}
                    )
            finally:
                try:
                    if response is not None and response.background is not None:
                        await call(response.background)
                        response.background = None
                finally:
                    request.state.pop("_higuma_dispatching", None)
                    try:
                        request.close()
                    finally:
                        _pop_request(token)

    async def disconnected():
        while (await receive())["type"] != "http.disconnect":
            pass

    task = asyncio.create_task(execute())
    watcher = asyncio.create_task(disconnected())
    peer_gone = False
    try:
        done, _ = await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if watcher in done:
            watcher.result()
            peer_gone = True
            if not task.done():
                task.cancel()
        await task
    except asyncio.CancelledError:
        if not peer_gone:
            raise
    finally:
        if not task.done():
            task.cancel()
        watcher.cancel()
        await asyncio.gather(task, watcher, return_exceptions=True)
