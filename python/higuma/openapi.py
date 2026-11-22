from __future__ import annotations

import inspect
import json
import types
from collections.abc import Mapping, Sequence
from dataclasses import MISSING, fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from html import escape
from typing import (
    Annotated,
    Any,
    Literal,
    Union,
    get_args,
    get_origin,
    is_typeddict,
)
from uuid import UUID

from .parameters import (
    Body,
    Cookie,
    Depends,
    File,
    Form,
    Header,
    Parameter,
    PathParam,
    QueryParam,
    parameter_schema_metadata,
    resolved_model_hints,
    resolved_parameter_hints,
    serialize_json_value,
    split_parameter_annotation,
)
from .request import UploadFile


def generate_openapi(app: Any) -> dict[str, Any]:
    paths: dict[str, dict[str, Any]] = {}
    schemas: dict[str, Any] = {}
    registry: dict[str, Any] = {}
    operation_ids: set[str] = set()
    for route in app._routes:
        if not route.include_in_schema:
            continue
        path = _openapi_path(route.rule)
        path_item = paths.setdefault(path, {})
        for method in route.methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            operation = _operation_for(route, method, schemas, registry)
            operation_id = str(operation["operationId"])
            if operation_id in operation_ids:
                operation_id = f"{operation_id}_{method.lower()}"
                suffix = 2
                candidate = operation_id
                while candidate in operation_ids:
                    candidate = f"{operation_id}_{suffix}"
                    suffix += 1
                operation["operationId"] = candidate
                operation_id = candidate
            operation_ids.add(operation_id)
            path_item[method.lower()] = operation

    document: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {
            "title": app.config.get("OPENAPI_TITLE", app.import_name),
            "version": app.config.get("OPENAPI_VERSION", "0.0.0"),
            "description": app.config.get("OPENAPI_DESCRIPTION", ""),
        },
        "paths": paths,
    }
    if schemas:
        document["components"] = {"schemas": schemas}
    servers = app.config.get("OPENAPI_SERVERS")
    if servers:
        document["servers"] = servers
    return document


def swagger_ui_html(openapi_url: str, title: str) -> str:
    safe_title = escape(title)
    safe_url = json.dumps(openapi_url).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui.css">
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
  <script>
    SwaggerUIBundle({{
      url: {safe_url},
      dom_id: "#swagger-ui",
      deepLinking: true,
      displayRequestDuration: true
    }});
  </script>
</body>
</html>"""


def _register_schema(
    annotation: Any,
    schemas: dict[str, Any],
    registry: dict[str, Any],
) -> tuple[str, bool]:
    for existing_name, existing_type in registry.items():
        if existing_type is annotation:
            return existing_name, True
    base = annotation.__name__
    if base in schemas and base not in registry:
        registry[base] = annotation
        return base, True
    name = base
    suffix = 2
    while name in schemas or name in registry:
        name = f"{base}_{suffix}"
        suffix += 1
    registry[name] = annotation
    schemas[name] = {}
    return name, False


def _body_media_type(annotation: Any) -> str:
    origin = get_origin(annotation)
    if origin is Annotated:
        args = get_args(annotation)
        if args:
            return _body_media_type(args[0])
    if origin in {types.UnionType, Union}:
        non_none = [item for item in get_args(annotation) if item is not type(None)]
        if len(non_none) == 1:
            return _body_media_type(non_none[0])
    if annotation is bytes:
        return "application/octet-stream"
    if annotation is str:
        return "text/plain"
    return "application/json"


def schema_for(
    annotation: Any,
    schemas: dict[str, Any] | None = None,
    *,
    _registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    schemas = schemas if schemas is not None else {}
    registry = _registry if _registry is not None else {}
    if (
        annotation is inspect.Parameter.empty
        or annotation is inspect.Signature.empty
        or annotation is Any
    ):
        return {}
    if annotation is None or annotation is type(None):
        return {"type": "null"}

    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Annotated:
        annotated_schema = schema_for(args[0], schemas, _registry=registry)
        for metadata in args[1:]:
            if isinstance(metadata, Parameter):
                annotated_schema = {**annotated_schema, **parameter_schema_metadata(metadata)}
            elif isinstance(metadata, Mapping):
                annotated_schema = {**annotated_schema, **dict(metadata)}
        return annotated_schema
    if str(origin) in {"typing.Required", "typing.NotRequired"}:
        return schema_for(args[0] if args else Any, schemas, _registry=registry)
    if origin in {list, set, frozenset, Sequence}:
        schema = {
            "type": "array",
            "items": schema_for(args[0] if args else Any, schemas, _registry=registry),
        }
        if origin in {set, frozenset}:
            schema["uniqueItems"] = True
        return schema
    if origin is tuple:
        if args and args[-1] is not Ellipsis and args != ((),):
            return {
                "type": "array",
                "prefixItems": [schema_for(item, schemas, _registry=registry) for item in args],
                "minItems": len(args),
                "maxItems": len(args),
            }
        return {
            "type": "array",
            "items": schema_for(
                args[0] if args and args != ((),) else Any, schemas, _registry=registry
            ),
        }
    if origin in {dict, Mapping}:
        return {
            "type": "object",
            "additionalProperties": schema_for(
                args[1] if len(args) > 1 else Any, schemas, _registry=registry
            ),
        }
    if origin in {types.UnionType, Union}:
        options = [schema_for(item, schemas, _registry=registry) for item in args]
        return {"anyOf": options}
    if origin is Literal:
        values = list(args)
        schema: dict[str, Any] = {"enum": values}
        if values and all(isinstance(value, str) for value in values):
            schema["type"] = "string"
        elif values and all(isinstance(value, bool) for value in values):
            schema["type"] = "boolean"
        elif values and all(
            isinstance(value, int) and not isinstance(value, bool) for value in values
        ):
            schema["type"] = "integer"
        elif values and all(
            isinstance(value, (int, float)) and not isinstance(value, bool) for value in values
        ):
            schema["type"] = "number"
        return schema

    if annotation in {list, tuple, set, frozenset, Sequence}:
        schema = {"type": "array", "items": {}}
        if annotation in {set, frozenset}:
            schema["uniqueItems"] = True
        return schema
    if annotation in {dict, Mapping}:
        return {"type": "object", "additionalProperties": {}}

    primitive = {
        str: {"type": "string"},
        int: {"type": "integer"},
        float: {"type": "number"},
        bool: {"type": "boolean"},
        bytes: {"type": "string", "format": "binary"},
        UploadFile: {"type": "string", "format": "binary"},
        UUID: {"type": "string", "format": "uuid"},
        date: {"type": "string", "format": "date"},
        datetime: {"type": "string", "format": "date-time"},
    }.get(annotation)
    if primitive is not None:
        return primitive
    if inspect.isclass(annotation) and issubclass(annotation, Enum):
        values = [member.value for member in annotation]
        schema = {"enum": values}
        if values and all(isinstance(value, str) for value in values):
            schema["type"] = "string"
        elif values and all(isinstance(value, bool) for value in values):
            schema["type"] = "boolean"
        elif values and all(
            isinstance(value, int) and not isinstance(value, bool) for value in values
        ):
            schema["type"] = "integer"
        elif values and all(
            isinstance(value, (int, float)) and not isinstance(value, bool) for value in values
        ):
            schema["type"] = "number"
        return schema

    if is_typeddict(annotation):
        name, already_defined = _register_schema(annotation, schemas, registry)
        if already_defined:
            return {"$ref": f"#/components/schemas/{name}"}
        type_hints = resolved_model_hints(annotation) or dict(annotation.__annotations__)
        properties = {
            field_name: schema_for(field_annotation, schemas, _registry=registry)
            for field_name, field_annotation in type_hints.items()
        }
        required_keys = sorted(getattr(annotation, "__required_keys__", ()))
        schemas[name] = {
            "type": "object",
            "properties": properties,
            **({"required": required_keys} if required_keys else {}),
        }
        return {"$ref": f"#/components/schemas/{name}"}

    if inspect.isclass(annotation) and is_dataclass(annotation):
        name, already_defined = _register_schema(annotation, schemas, registry)
        if already_defined:
            return {"$ref": f"#/components/schemas/{name}"}
        field_hints = resolved_model_hints(annotation)
        properties = {}
        required = []
        for item in fields(annotation):
            if not item.init:
                continue
            properties[item.name] = schema_for(
                field_hints.get(item.name, item.type),
                schemas,
                _registry=registry,
            )
            if item.default is MISSING and item.default_factory is MISSING:
                required.append(item.name)
        schemas[name] = {
            "type": "object",
            "properties": properties,
            **({"required": required} if required else {}),
        }
        return {"$ref": f"#/components/schemas/{name}"}

    if inspect.isclass(annotation) and hasattr(annotation, "__fields__"):
        name, already_defined = _register_schema(annotation, schemas, registry)
        if already_defined:
            return {"$ref": f"#/components/schemas/{name}"}
        properties = {
            field_name: _orm_field_schema(field)
            for field_name, field in annotation.__fields__.items()
        }
        schemas[name] = {"type": "object", "properties": properties}
        return {"$ref": f"#/components/schemas/{name}"}
    return {"type": "string"}


def _target_view_func(view_func: Any, method: str) -> Any:
    view_class = getattr(view_func, "view_class", None)
    if inspect.isclass(view_class):
        method_handler = getattr(view_class, method.lower(), None)
        if callable(method_handler):
            return method_handler
        dispatch_handler = getattr(view_class, "dispatch_request", None)
        if callable(dispatch_handler):
            return dispatch_handler
    return view_func


def _is_required_parameter(parameter: inspect.Parameter) -> bool:
    return parameter.default is inspect.Parameter.empty or isinstance(
        parameter.default, (Parameter, Depends)
    )


def _operation_for(
    route: Any,
    method: str,
    schemas: dict[str, Any],
    registry: dict[str, Any],
) -> dict[str, Any]:
    metadata = dict(route.openapi)
    target_func = _target_view_func(route.view_func, method)
    signature = inspect.signature(target_func)
    type_hints = resolved_parameter_hints(target_func)
    description = (
        metadata.pop("description", None)
        or inspect.getdoc(target_func)
        or inspect.getdoc(route.view_func)
        or ""
    )
    summary = metadata.pop("summary", None)
    success_status = str(metadata.pop("status_code", 200))
    response_model = metadata.pop("response_model", None)
    operation: dict[str, Any] = {
        "operationId": metadata.pop("operation_id", route.endpoint),
        "responses": metadata.pop(
            "responses",
            {
                success_status: {
                    "description": "Successful response",
                    "content": {
                        "application/json": {
                            "schema": schema_for(
                                response_model
                                if response_model is not None
                                else type_hints.get("return", signature.return_annotation),
                                schemas,
                                _registry=registry,
                            )
                        }
                    },
                }
            },
        ),
    }
    if summary:
        operation["summary"] = summary
    if description:
        operation["description"] = description
    tags = metadata.pop("tags", None)
    if tags:
        operation["tags"] = list(tags)

    input_specs = list(_input_specs(target_func))
    parameters = []
    for name, converter in route.converters.items():
        annotation = type_hints.get(
            name,
            signature.parameters.get(
                name, inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            ).annotation,
        )
        _, declared_marker = split_parameter_annotation(
            annotation,
            signature.parameters.get(
                name, inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            ).default,
        )
        schema = schema_for(annotation, schemas, _registry=registry) or _converter_schema(converter)
        if isinstance(declared_marker, PathParam):
            schema.update(parameter_schema_metadata(declared_marker))
        parameters.append({"name": name, "in": "path", "required": True, "schema": schema})

    for parameter, annotation, marker in input_specs:
        if not isinstance(marker, (QueryParam, Header, Cookie)):
            continue
        alias = marker.alias or parameter.name
        if isinstance(marker, Header) and marker.alias is None:
            alias = alias.replace("_", "-")
        schema = schema_for(annotation, schemas, _registry=registry)
        schema.update(parameter_schema_metadata(marker))
        required = _is_required_parameter(parameter)
        if not required:
            schema["default"] = serialize_json_value(parameter.default)
        item: dict[str, Any] = {
            "name": alias,
            "in": marker.location,
            "required": required,
            "schema": schema,
        }
        if marker.description:
            item["description"] = marker.description
        if marker.deprecated:
            item["deprecated"] = True
        parameters.append(item)
    if parameters:
        operation["parameters"] = list(
            {(item["in"], item["name"]): item for item in parameters}.values()
        )

    request_body = metadata.pop("request_body", None)
    if request_body is not None and method not in {"GET", "HEAD"}:
        operation["requestBody"] = {
            "required": True,
            "content": {
                _body_media_type(request_body): {
                    "schema": schema_for(request_body, schemas, _registry=registry)
                }
            },
        }
    elif method not in {"GET", "HEAD"}:
        body_specs = [item for item in input_specs if isinstance(item[2], Body)]
        multipart_specs = [item for item in input_specs if isinstance(item[2], (Form, File))]
        if body_specs:
            parameter, annotation, marker = body_specs[0]
            operation["requestBody"] = {
                "required": _is_required_parameter(parameter),
                "content": {
                    _body_media_type(annotation): {
                        "schema": {
                            **schema_for(annotation, schemas, _registry=registry),
                            **parameter_schema_metadata(marker),
                        }
                    }
                },
            }
        elif multipart_specs:
            properties = {}
            required_fields = []
            for parameter, annotation, marker in multipart_specs:
                alias = marker.alias or parameter.name
                if isinstance(marker, File):
                    schema = (
                        {"type": "string", "format": "binary"}
                        if annotation in (inspect.Parameter.empty, Any, UploadFile, bytes)
                        else schema_for(annotation, schemas, _registry=registry)
                    )
                else:
                    schema = schema_for(annotation, schemas, _registry=registry)
                schema.update(parameter_schema_metadata(marker))
                properties[alias] = schema
                if _is_required_parameter(parameter):
                    required_fields.append(alias)
            multipart_schema: dict[str, Any] = {"type": "object", "properties": properties}
            if required_fields:
                multipart_schema["required"] = required_fields
            operation["requestBody"] = {
                "required": bool(required_fields),
                "content": {"multipart/form-data": {"schema": multipart_schema}},
            }
    if input_specs:
        operation["responses"].setdefault(
            "422",
            {"description": "Request validation failed"},
        )
    operation.update(metadata.pop("openapi_extra", {}))
    return operation


def _input_specs(
    func: Any,
    seen: set[Any] | None = None,
) -> list[tuple[inspect.Parameter, Any, Parameter | Depends]]:
    seen = set() if seen is None else seen
    if func in seen:
        return []
    seen.add(func)
    signature = inspect.signature(func)
    hints = resolved_parameter_hints(func)
    result = []
    for parameter in signature.parameters.values():
        annotation = hints.get(parameter.name, parameter.annotation)
        base_annotation, marker = split_parameter_annotation(annotation, parameter.default)
        if isinstance(marker, Depends):
            result.extend(_input_specs(marker.dependency, seen))
        elif isinstance(marker, Parameter):
            result.append((parameter, base_annotation, marker))
    return result


def _openapi_path(rule: str) -> str:
    parts = []
    for segment in rule.split("/"):
        if segment.startswith("<") and segment.endswith(">"):
            parts.append("{" + segment[1:-1].split(":")[-1] + "}")
        else:
            parts.append(segment)
    return "/".join(parts) or "/"


def _converter_schema(converter: str) -> dict[str, Any]:
    return {
        "int": {"type": "integer"},
        "float": {"type": "number"},
        "uuid": {"type": "string", "format": "uuid"},
        "path": {"type": "string"},
        "string": {"type": "string"},
        "str": {"type": "string"},
    }.get(converter, {"type": "string"})


def _orm_field_schema(field: Any) -> dict[str, Any]:
    name = type(field).__name__
    schema = {
        "Integer": {"type": "integer"},
        "Float": {"type": "number"},
        "Boolean": {"type": "boolean"},
        "DateTime": {"type": "string", "format": "date-time"},
        "Date": {"type": "string", "format": "date"},
        "Blob": {"type": "string", "format": "binary"},
    }.get(name, {"type": "string"})
    if field.nullable:
        return {"anyOf": [schema, {"type": "null"}]}
    return schema
