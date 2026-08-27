from __future__ import annotations

import inspect
import math
import re
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import MISSING, asdict, dataclass, fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal, Union, get_args, get_origin, get_type_hints
from uuid import UUID

from .background import BackgroundTasks
from .exceptions import BadRequest, UnsupportedMediaType
from .request import Request, UploadFile


@dataclass(frozen=True, slots=True)
class Parameter:
    alias: str | None = None
    title: str | None = None
    description: str | None = None
    deprecated: bool = False
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None
    ge: float | None = None
    gt: float | None = None
    le: float | None = None
    lt: float | None = None

    location: ClassVar[str]

    def __post_init__(self) -> None:
        if self.min_length is not None and self.min_length < 0:
            raise ValueError("min_length must be non-negative")
        if self.max_length is not None and self.max_length < 0:
            raise ValueError("max_length must be non-negative")
        if (
            self.min_length is not None
            and self.max_length is not None
            and self.min_length > self.max_length
        ):
            raise ValueError("min_length must not exceed max_length")
        if self.pattern is not None:
            re.compile(self.pattern)


class QueryParam(Parameter):
    location = "query"


class Header(Parameter):
    location = "header"


class Cookie(Parameter):
    location = "cookie"


class PathParam(Parameter):
    location = "path"


class Body(Parameter):
    location = "body"


class Form(Parameter):
    location = "form"


class File(Parameter):
    location = "file"


@dataclass(frozen=True, slots=True)
class Depends:
    dependency: Callable[..., Any]
    use_cache: bool = True

    def __post_init__(self) -> None:
        if not callable(self.dependency):
            raise TypeError("dependency must be callable")


class RequestValidationError(ValueError):
    def __init__(self, errors: Sequence[Mapping[str, Any]]) -> None:
        self.errors = [dict(error) for error in errors]
        super().__init__("request validation failed")


class ResponseValidationError(RuntimeError):
    pass


def cache_parameter_hints(
    func: Callable[..., Any],
    localns: Mapping[str, Any] | None = None,
    seen: set[Callable[..., Any]] | None = None,
) -> None:
    seen = set() if seen is None else seen
    if func in seen:
        return
    seen.add(func)
    try:
        hints = get_type_hints(
            func,
            globalns=getattr(func, "__globals__", None),
            localns=dict(localns or {}),
            include_extras=True,
        )
    except (NameError, TypeError):
        return
    try:
        func.__higuma_type_hints__ = hints
    except (AttributeError, TypeError):
        return
    signature = inspect.signature(func)
    for parameter in signature.parameters.values():
        annotation = hints.get(parameter.name, parameter.annotation)
        _, marker = split_parameter_annotation(annotation, parameter.default)
        if isinstance(marker, Depends):
            cache_parameter_hints(marker.dependency, localns, seen)


def resolve_arguments(
    func: Callable[..., Any],
    request: Request,
    supplied: Mapping[str, Any],
    await_value: Callable[[Any], Any],
    *,
    dependency_mode: bool = False,
    dependency_stack: tuple[Callable[..., Any], ...] = (),
) -> tuple[list[Any], dict[str, Any]]:
    signature = inspect.signature(func)
    type_hints = resolved_parameter_hints(func)

    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []
    unresolved: list[inspect.Parameter] = []
    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )

    for parameter in signature.parameters.values():
        if parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        annotation = type_hints.get(parameter.name, parameter.annotation)
        base_annotation, marker = split_parameter_annotation(annotation, parameter.default)
        value = _MISSING
        location = "path"
        alias = parameter.name

        if isinstance(marker, Depends):
            value = _resolve_dependency(
                marker,
                request,
                supplied,
                await_value,
                dependency_stack,
            )
            location = "dependency"
        elif isinstance(marker, Parameter):
            location = marker.location
            alias = marker.alias or parameter.name
            if isinstance(marker, Header) and marker.alias is None:
                alias = alias.replace("_", "-")
            try:
                value = _read_parameter(request, supplied, marker, alias, base_annotation)
            except (BadRequest, UnsupportedMediaType) as exc:
                errors.append(_validation_error(location, alias, exc.detail, _MISSING))
                continue
        elif parameter.name in supplied:
            value = supplied[parameter.name]
        elif base_annotation is BackgroundTasks:
            value = request.state.setdefault("_higuma_background_tasks", BackgroundTasks())
            location = "background"
        elif base_annotation is Request or parameter.name == "request":
            value = request
            location = "request"
        elif parameter.default is not inspect.Parameter.empty:
            continue
        else:
            unresolved.append(parameter)
            continue

        if value is _MISSING:
            if parameter.default is not inspect.Parameter.empty and not isinstance(
                parameter.default, (Parameter, Depends)
            ):
                continue
            errors.append(_validation_error(location, alias, "Field required", value))
            continue

        try:
            converted = _convert_value(value, base_annotation)
            if isinstance(marker, Parameter):
                _validate_constraints(converted, marker)
        except (TypeError, ValueError) as exc:
            errors.append(_validation_error(location, alias, str(exc), value))
            continue

        if parameter.kind == inspect.Parameter.POSITIONAL_ONLY:
            args.append(converted)
        else:
            kwargs[parameter.name] = converted

    if unresolved:
        if dependency_mode:
            errors.extend(
                _validation_error("dependency", item.name, "Field required", _MISSING)
                for item in unresolved
            )
        else:
            first = unresolved[0]
            if first.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            ):
                args.append(request)

    if accepts_kwargs:
        for name, value in supplied.items():
            kwargs.setdefault(name, value)

    if errors:
        raise RequestValidationError(errors)
    return args, kwargs


def split_parameter_annotation(
    annotation: Any,
    default: Any = inspect.Parameter.empty,
) -> tuple[Any, Parameter | Depends | None]:
    annotation, metadata = _unwrap_parameter_annotation(annotation)
    markers = [item for item in metadata if isinstance(item, (Parameter, Depends))]
    if isinstance(default, (Parameter, Depends)):
        markers.append(default)
    if len(markers) > 1:
        raise TypeError("a parameter may declare only one higuma input marker")
    return annotation, markers[0] if markers else None


def _unwrap_parameter_annotation(annotation: Any) -> tuple[Any, tuple[Any, ...]]:
    origin = get_origin(annotation)
    if origin is Annotated:
        base_annotation, *metadata = get_args(annotation)
        return base_annotation, tuple(metadata)
    if origin not in (types.UnionType, Union):
        return annotation, ()

    options: list[Any] = []
    metadata: list[Any] = []
    changed = False
    for option in get_args(annotation):
        base_annotation, option_metadata = _unwrap_parameter_annotation(option)
        options.append(base_annotation)
        metadata.extend(option_metadata)
        changed = changed or bool(option_metadata)
    if not changed:
        return annotation, ()

    unique_options = list(dict.fromkeys(options))
    base_annotation = unique_options[0]
    for option in unique_options[1:]:
        base_annotation = base_annotation | option
    return base_annotation, tuple(metadata)


def resolved_parameter_hints(func: Callable[..., Any]) -> dict[str, Any]:
    cached = getattr(func, "__higuma_type_hints__", None)
    if isinstance(cached, dict):
        return cached
    try:
        return get_type_hints(func, include_extras=True)
    except (NameError, TypeError):
        return {}


def parameter_schema_metadata(marker: Parameter) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for source, target in (
        ("title", "title"),
        ("description", "description"),
        ("min_length", "minLength"),
        ("max_length", "maxLength"),
        ("pattern", "pattern"),
        ("ge", "minimum"),
        ("gt", "exclusiveMinimum"),
        ("le", "maximum"),
        ("lt", "exclusiveMaximum"),
    ):
        value = getattr(marker, source)
        if value is not None:
            metadata[target] = value
    return metadata


def serialize_json_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {key: serialize_json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): serialize_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [serialize_json_value(item) for item in value]
    if isinstance(value, Enum):
        return serialize_json_value(value.value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (UUID, Path)):
        return str(value)
    return value


def coerce_response_model(value: Any, annotation: Any) -> Any:
    try:
        if is_dataclass(annotation) and isinstance(value, Mapping):
            names = {field.name for field in fields(annotation) if field.init}
            value = {key: item for key, item in value.items() if key in names}
        elif _is_typed_dict(annotation) and isinstance(value, Mapping):
            value = {key: item for key, item in value.items() if key in annotation.__annotations__}
        return serialize_json_value(_convert_value(value, annotation))
    except (TypeError, ValueError) as exc:
        raise ResponseValidationError(f"response model validation failed: {exc}") from exc


def _read_parameter(
    request: Request,
    supplied: Mapping[str, Any],
    marker: Parameter,
    alias: str,
    annotation: Any,
) -> Any:
    if isinstance(marker, PathParam):
        return supplied.get(alias, _MISSING)
    if isinstance(marker, QueryParam):
        if _is_collection_annotation(annotation):
            values = request.args.getlist(alias)
            return values if values else _MISSING
        return request.args.get(alias, _MISSING)
    if isinstance(marker, Header):
        return request.headers.get(alias, _MISSING)
    if isinstance(marker, Cookie):
        return request.cookies.get(alias, _MISSING)
    if isinstance(marker, Body):
        if annotation is bytes:
            return request.body
        if annotation is str:
            return request.text
        if not request.body:
            return _MISSING
        return request.get_json()
    if isinstance(marker, Form):
        if _is_collection_annotation(annotation):
            values = request.form.getlist(alias)
            return values if values else _MISSING
        return request.form.get(alias, _MISSING)
    if isinstance(marker, File):
        if _is_collection_annotation(annotation):
            values = request.files.getlist(alias)
            return values if values else _MISSING
        return request.files.get(alias, _MISSING)
    raise TypeError(f"unsupported parameter marker: {type(marker).__name__}")


def _resolve_dependency(
    marker: Depends,
    request: Request,
    supplied: Mapping[str, Any],
    await_value: Callable[[Any], Any],
    stack: tuple[Callable[..., Any], ...],
) -> Any:
    declared_dependency = marker.dependency
    overrides = request.state.get("_higuma_dependency_overrides", {})
    dependency = overrides.get(declared_dependency, declared_dependency)
    if dependency in stack:
        names = " -> ".join(item.__name__ for item in (*stack, dependency))
        raise RuntimeError(f"circular dependency: {names}")
    cache = request.state.setdefault("_higuma_dependency_cache", {})
    if marker.use_cache and declared_dependency in cache:
        return cache[declared_dependency]

    args, kwargs = resolve_arguments(
        dependency,
        request,
        supplied,
        await_value,
        dependency_mode=True,
        dependency_stack=(*stack, dependency),
    )
    result = dependency(*args, **kwargs)
    cleanups = request.state.setdefault("_higuma_dependency_cleanups", [])
    if inspect.isgenerator(result):
        try:
            value = next(result)
        except StopIteration as exc:
            raise RuntimeError("dependency generator did not yield") from exc
        cleanups.append(lambda: _finish_generator(result))
    elif inspect.isasyncgen(result):
        try:
            value = await_value(result.__anext__())
        except StopAsyncIteration as exc:
            raise RuntimeError("async dependency generator did not yield") from exc
        cleanups.append(lambda: _finish_async_generator(result, await_value))
    else:
        value = await_value(result)

    if marker.use_cache:
        cache[declared_dependency] = value
    return value


def _finish_generator(generator: Any) -> None:
    try:
        next(generator)
    except StopIteration:
        return
    finally:
        generator.close()
    raise RuntimeError("dependency generator yielded more than once")


def _finish_async_generator(generator: Any, await_value: Callable[[Any], Any]) -> None:
    try:
        await_value(generator.__anext__())
    except StopAsyncIteration:
        return
    finally:
        await_value(generator.aclose())
    raise RuntimeError("async dependency generator yielded more than once")


def _convert_value(value: Any, annotation: Any) -> Any:
    if annotation in (Any, inspect.Parameter.empty, inspect.Signature.empty):
        return value
    if get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]

    origin = get_origin(annotation)
    args = get_args(annotation)
    if str(origin) in {"typing.Required", "typing.NotRequired"}:
        return _convert_value(value, args[0] if args else Any)
    if origin in (types.UnionType, Union):
        if value is None and type(None) in args:
            return None
        failures = []
        for option in args:
            if option is type(None):
                continue
            try:
                return _convert_value(value, option)
            except (TypeError, ValueError) as exc:
                failures.append(str(exc))
        raise ValueError("value does not match any allowed type: " + "; ".join(failures))
    if origin is Literal:
        for allowed in args:
            try:
                converted = _convert_value(value, type(allowed))
            except (TypeError, ValueError):
                continue
            if converted == allowed:
                return allowed
        raise ValueError(f"value must be one of {args!r}")
    if origin in (list, set, frozenset, Sequence):
        values = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
        converted = [_convert_value(item, args[0] if args else Any) for item in values]
        if origin is set:
            return set(converted)
        if origin is frozenset:
            return frozenset(converted)
        return converted
    if origin is tuple:
        values = value if isinstance(value, (list, tuple)) else [value]
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_convert_value(item, args[0]) for item in values)
        if args and len(values) != len(args):
            raise ValueError(f"tuple requires {len(args)} items")
        return tuple(
            _convert_value(item, args[index] if args else Any) for index, item in enumerate(values)
        )
    if origin in (dict, Mapping):
        if not isinstance(value, Mapping):
            raise TypeError("value must be an object")
        key_type, value_type = args if len(args) == 2 else (Any, Any)
        return {
            _convert_value(key, key_type): _convert_value(item, value_type)
            for key, item in value.items()
        }

    if annotation is str:
        if isinstance(value, str):
            return value
        raise TypeError("value must be a string")
    if annotation is bytes:
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode()
        raise TypeError("value must be bytes")
    if annotation is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        raise ValueError("value must be a boolean")
    if annotation is int:
        if isinstance(value, bool):
            raise TypeError("value must be an integer")
        if isinstance(value, float) and not value.is_integer():
            raise ValueError("value must be an integer")
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("value must be an integer") from exc
    if annotation is float:
        if isinstance(value, bool):
            raise TypeError("value must be a number")
        try:
            converted = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("value must be a number") from exc
        if not math.isfinite(converted):
            raise ValueError("value must be finite")
        return converted
    if annotation is UUID:
        try:
            return value if isinstance(value, UUID) else UUID(str(value))
        except ValueError as exc:
            raise ValueError("value must be a UUID") from exc
    if annotation is date:
        if isinstance(value, datetime):
            raise TypeError("value must be a date, not a date-time")
        try:
            return value if isinstance(value, date) else date.fromisoformat(str(value))
        except ValueError as exc:
            raise ValueError("value must be an ISO date") from exc
    if annotation is datetime:
        try:
            return (
                value
                if isinstance(value, datetime)
                else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            )
        except ValueError as exc:
            raise ValueError("value must be an ISO date-time") from exc
    if annotation is UploadFile:
        if not isinstance(value, UploadFile):
            raise TypeError("value must be an uploaded file")
        return value
    if inspect.isclass(annotation) and issubclass(annotation, Enum):
        try:
            return value if isinstance(value, annotation) else annotation(value)
        except ValueError:
            try:
                return annotation[str(value)]
            except KeyError as exc:
                raise ValueError(f"value must be a valid {annotation.__name__}") from exc
    if _is_typed_dict(annotation):
        return _convert_typed_dict(value, annotation)
    if is_dataclass(annotation):
        return _convert_dataclass(value, annotation)
    if inspect.isclass(annotation) and isinstance(value, annotation):
        return value
    return value


def _convert_dataclass(value: Any, annotation: Any) -> Any:
    if isinstance(value, annotation):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("value must be an object")
    try:
        hints = get_type_hints(annotation, include_extras=True)
    except (NameError, TypeError):
        hints = {}
    known = {field.name for field in fields(annotation) if field.init}
    extra = set(value) - known
    if extra:
        raise ValueError(f"unexpected fields: {', '.join(sorted(map(str, extra)))}")
    converted = {}
    for field in fields(annotation):
        if not field.init:
            continue
        if field.name in value:
            converted[field.name] = _convert_value(
                value[field.name], hints.get(field.name, field.type)
            )
        elif field.default is MISSING and field.default_factory is MISSING:
            raise ValueError(f"missing field: {field.name}")
    return annotation(**converted)


def _convert_typed_dict(value: Any, annotation: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("value must be an object")
    try:
        hints = get_type_hints(annotation, include_extras=True)
    except (NameError, TypeError):
        hints = dict(annotation.__annotations__)
    extra = set(value) - set(hints)
    if extra:
        raise ValueError(f"unexpected fields: {', '.join(sorted(map(str, extra)))}")
    missing = set(getattr(annotation, "__required_keys__", ())) - set(value)
    if missing:
        raise ValueError(f"missing fields: {', '.join(sorted(missing))}")
    return {key: _convert_value(item, hints[key]) for key, item in value.items() if key in hints}


def _validate_constraints(value: Any, marker: Parameter) -> None:
    if marker.min_length is not None:
        try:
            if len(value) < marker.min_length:
                raise ValueError(f"value must contain at least {marker.min_length} items")
        except TypeError as exc:
            raise TypeError("min_length requires a sized value") from exc
    if marker.max_length is not None:
        try:
            if len(value) > marker.max_length:
                raise ValueError(f"value must contain at most {marker.max_length} items")
        except TypeError as exc:
            raise TypeError("max_length requires a sized value") from exc
    if marker.pattern is not None and re.fullmatch(marker.pattern, str(value)) is None:
        raise ValueError(f"value must match pattern {marker.pattern!r}")
    for boundary, comparison, message in (
        (marker.ge, lambda left, right: left >= right, "greater than or equal to"),
        (marker.gt, lambda left, right: left > right, "greater than"),
        (marker.le, lambda left, right: left <= right, "less than or equal to"),
        (marker.lt, lambda left, right: left < right, "less than"),
    ):
        if boundary is not None:
            try:
                valid = comparison(value, boundary)
            except TypeError as exc:
                raise TypeError("numeric constraints require a comparable value") from exc
            if not valid:
                raise ValueError(f"value must be {message} {boundary}")


def _validation_error(location: str, name: str, message: str, value: Any) -> dict[str, Any]:
    error = {"loc": [location, name], "msg": message, "type": "value_error"}
    if value is not _MISSING:
        error["input"] = (
            "***" if location == "cookie" or _is_sensitive_name(name) else _safe_input(value)
        )
    return error


def _safe_input(value: Any) -> Any:
    if isinstance(value, UploadFile):
        return {"filename": value.filename, "content_type": value.content_type, "size": value.size}
    rendered = _redact_sensitive_values(serialize_json_value(value))
    text = repr(rendered)
    return rendered if len(text) <= 256 else text[:253] + "..."


def _redact_sensitive_values(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "***" if _is_sensitive_name(str(key)) else _redact_sensitive_values(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_values(item) for item in value]
    return value


def _is_sensitive_name(name: str) -> bool:
    normalized = name.lower().replace("-", "_")
    return any(
        marker in normalized
        for marker in ("password", "passwd", "token", "secret", "authorization", "api_key")
    )


def _is_collection_annotation(annotation: Any) -> bool:
    origin = get_origin(annotation)
    if origin is Annotated:
        return _is_collection_annotation(get_args(annotation)[0])
    if origin in (types.UnionType, Union):
        return any(_is_collection_annotation(item) for item in get_args(annotation))
    return origin in (list, tuple, set, frozenset, Sequence)


def _is_typed_dict(annotation: Any) -> bool:
    return (
        inspect.isclass(annotation)
        and issubclass(annotation, dict)
        and hasattr(annotation, "__required_keys__")
        and hasattr(annotation, "__optional_keys__")
    )


_MISSING = object()
