from __future__ import annotations

import inspect
import importlib
import json
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from ._core import HigumaCore


@dataclass(slots=True)
class TemplateResponse:
    template: str
    context: dict[str, Any] = field(default_factory=dict)
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    __higuma_template__: bool = field(default=True, init=False)

    @property
    def context_json(self) -> str:
        return json.dumps(self.context, ensure_ascii=False, default=str)


def render_template(template: str, /, **context: Any) -> TemplateResponse:
    return TemplateResponse(template=template, context=context)


def _adapt_handler(func: Callable[..., Any]) -> Callable[[dict[str, Any]], Any]:
    if inspect.iscoroutinefunction(func):
        raise TypeError("async handler is not supported yet. define a normal function.")

    signature = inspect.signature(func)
    accepts_request = len(signature.parameters) > 0

    @wraps(func)
    def wrapper(request: dict[str, Any]) -> Any:
        if accepts_request:
            return func(request)
        return func()

    return wrapper


class Higuma:
    def __init__(self, import_name: str, template_folder: str = "templates") -> None:
        self.import_name = import_name
        resolved = _resolve_template_folder(import_name, template_folder)
        self._core = HigumaCore(resolved)

    def route(self, rule: str, methods: list[str] | tuple[str, ...] | None = None) -> Callable:
        method_list = [m.upper() for m in (methods or ["GET"])]

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            adapted = _adapt_handler(func)
            self._core.add_route(rule, method_list, adapted)
            return func

        return decorator

    def get(self, rule: str) -> Callable:
        return self.route(rule, methods=["GET"])

    def post(self, rule: str) -> Callable:
        return self.route(rule, methods=["POST"])

    def put(self, rule: str) -> Callable:
        return self.route(rule, methods=["PUT"])

    def patch(self, rule: str) -> Callable:
        return self.route(rule, methods=["PATCH"])

    def delete(self, rule: str) -> Callable:
        return self.route(rule, methods=["DELETE"])

    def run(self, host: str = "127.0.0.1", port: int = 8000, workers: int = 0) -> None:
        self._core.run(host, port, workers)

    def render_template(
        self,
        template: str,
        /,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        **context: Any,
    ) -> TemplateResponse:
        return TemplateResponse(
            template=template,
            context=context,
            status=status,
            headers=headers or {},
        )

    def jsonify(
        self,
        payload: dict[str, Any] | list[Any],
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any] | list[Any], int, dict[str, str]]:
        result_headers = {"content-type": "application/json; charset=utf-8"}
        if headers:
            result_headers.update(headers)
        return payload, status, result_headers


__all__ = ["Higuma", "TemplateResponse", "render_template"]


def _resolve_template_folder(import_name: str, template_folder: str) -> str:
    folder = Path(template_folder)
    if folder.is_absolute():
        return str(folder)

    base = Path.cwd()
    if import_name == "__main__":
        return str((base / folder).resolve())

    try:
        module = importlib.import_module(import_name)
        module_file = getattr(module, "__file__", None)
        if module_file:
            base = Path(module_file).resolve().parent
    except Exception:
        pass

    return str((base / folder).resolve())
