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
    get_args,
    get_origin,
    get_type_hints,
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
    resolved_parameter_hints,
    serialize_json_value,
    split_parameter_annotation,
)


def generate_openapi(app: Any) -> dict[str, Any]:
    paths: dict[str, dict[str, Any]] = {}
    schemas: dict[str, Any] = {}
    operation_ids: set[str] = set()
    for route in app._routes:
        if not route.include_in_schema:
            continue
        path = _openapi_path(route.rule)
        path_item = paths.setdefault(path, {})
        for method in route.methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            operation = _operation_for(route, method, schemas)
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


def schema_for(annotation: Any, schemas: dict[str, Any] | None = None) -> dict[str, Any]:
    schemas = schemas if schemas is not None else {}
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
        annotated_schema = schema_for(args[0], schemas)
        for metadata in args[1:]:
            if isinstance(metadata, Mapping):
                annotated_schema = {**annotated_schema, **dict(metadata)}
        return annotated_schema
    if str(origin) in {"typing.Required", "typing.NotRequired"}:
        return schema_for(args[0] if args else Any, schemas)
    if origin in {list, set, frozenset, Sequence}:
        schema = {
            "type": "array",
            "items": schema_for(args[0] if args else Any, schemas),
        }
        if origin in {set, frozenset}:
            schema["uniqueItems"] = True
        return schema
    if origin is tuple:
        if len(args) > 1 and args[-1] is not Ellipsis:
            return {
                "type": "array",
                "prefixItems": [schema_for(item, schemas) for item in args],
                "minItems": len(args),
                "maxItems": len(args),
            }
        return {
            "type": "array",
            "items": schema_for(args[0] if args else Any, schemas),
        }
    if origin in {dict, Mapping}:
        return {
            "type": "object",
            "additionalProperties": schema_for(args[1] if len(args) > 1 else Any, schemas),
        }
    if origin in {types.UnionType, __import__("typing").Union}:
        options = [schema_for(item, schemas) for item in args]
        return {"anyOf": options}
    if origin is Literal:
        values = list(args)
        schema: dict[str, Any] = {"enum": values}
        if values and all(isinstance(value, str) for value in values):
            schema["type"] = "string"
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
        UUID: {"type": "string", "format": "uuid"},
        date: {"type": "string", "format": "date"},
        datetime: {"type": "string", "format": "date-time"},
    }.get(annotation)
    if primitive is not None:
        return primitive
    if inspect.isclass(annotation) and issubclass(annotation, Enum):
        values = [member.value for member in annotation]
        schema_type = (
            "integer" if values and all(isinstance(value, int) for value in values) else "string"
        )
        return {"type": schema_type, "enum": values}

    if is_typeddict(annotation):
        name = annotation.__name__
        if name in schemas:
            return {"$ref": f"#/components/schemas/{name}"}
        schemas[name] = {}
        try:
            type_hints = get_type_hints(annotation, include_extras=True)
        except (NameError, TypeError):
            type_hints = dict(annotation.__annotations__)
        properties = {
            field_name: schema_for(field_annotation, schemas)
            for field_name, field_annotation in type_hints.items()
        }
        required_keys = sorted(getattr(annotation, "__required_keys__", ()))
        schemas[name] = {
            "type": "object",
            "properties": properties,
            **({"required": required_keys} if required_keys else {}),
        }
        return {"$ref": f"#/components/schemas/{name}"}

    if is_dataclass(annotation):
        name = annotation.__name__
        if name in schemas:
            return {"$ref": f"#/components/schemas/{name}"}
        schemas[name] = {}
        try:
            field_hints = get_type_hints(annotation, include_extras=True)
        except (NameError, TypeError):
            field_hints = {}
        properties = {}
        required = []
        for item in fields(annotation):
            properties[item.name] = schema_for(
                field_hints.get(item.name, item.type),
                schemas,
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
        name = annotation.__name__
        if name in schemas:
            return {"$ref": f"#/components/schemas/{name}"}
        schemas[name] = {}
        properties = {
            field_name: _orm_field_schema(field)
            for field_name, field in annotation.__fields__.items()
        }
        schemas[name] = {"type": "object", "properties": properties}
        return {"$ref": f"#/components/schemas/{name}"}
    return {"type": "string"}


def _operation_for(route: Any, method: str, schemas: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(route.openapi)
    signature = inspect.signature(route.view_func)
    type_hints = resolved_parameter_hints(route.view_func)
    description = metadata.pop("description", None) or inspect.getdoc(route.view_func) or ""
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

    input_specs = list(_input_specs(route.view_func))
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
        schema = schema_for(annotation, schemas) or _converter_schema(converter)
        if isinstance(declared_marker, PathParam):
            schema.update(parameter_schema_metadata(declared_marker))
        parameters.append({"name": name, "in": "path", "required": True, "schema": schema})

    for parameter, annotation, marker in input_specs:
        if not isinstance(marker, (QueryParam, Header, Cookie)):
            continue
        alias = marker.alias or parameter.name
        if isinstance(marker, Header) and marker.alias is None:
            alias = alias.replace("_", "-")
        schema = schema_for(annotation, schemas)
        schema.update(parameter_schema_metadata(marker))
        required = parameter.default is inspect.Parameter.empty or isinstance(
            parameter.default, (Parameter, Depends)
        )
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
            "content": {"application/json": {"schema": schema_for(request_body, schemas)}},
        }
    elif method not in {"GET", "HEAD"}:
        body_specs = [item for item in input_specs if isinstance(item[2], Body)]
        multipart_specs = [item for item in input_specs if isinstance(item[2], (Form, File))]
        if body_specs:
            parameter, annotation, marker = body_specs[0]
            operation["requestBody"] = {
                "required": parameter.default is inspect.Parameter.empty,
                "content": {
                    "application/json": {
                        "schema": {
                            **schema_for(annotation, schemas),
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
                schema = (
                    {"type": "string", "format": "binary"}
                    if isinstance(marker, File)
                    else schema_for(annotation, schemas)
                )
                schema.update(parameter_schema_metadata(marker))
                properties[alias] = schema
                if parameter.default is inspect.Parameter.empty:
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
