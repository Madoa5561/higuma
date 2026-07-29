from __future__ import annotations

import inspect
import json
import types
from dataclasses import MISSING, fields, is_dataclass
from enum import Enum
from html import escape
from typing import Any, Literal, get_args, get_origin, get_type_hints


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
    if annotation in {inspect.Parameter.empty, inspect.Signature.empty, Any}:
        return {}
    if annotation is None or annotation is type(None):
        return {"type": "null"}

    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in {list, tuple, set, frozenset}:
        return {
            "type": "array",
            "items": schema_for(args[0] if args else Any, schemas),
        }
    if origin is dict:
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

    primitive = {
        str: {"type": "string"},
        int: {"type": "integer"},
        float: {"type": "number"},
        bool: {"type": "boolean"},
        bytes: {"type": "string", "format": "binary"},
    }.get(annotation)
    if primitive is not None:
        return primitive
    if inspect.isclass(annotation) and issubclass(annotation, Enum):
        values = [member.value for member in annotation]
        schema_type = (
            "integer" if values and all(isinstance(value, int) for value in values) else "string"
        )
        return {"type": schema_type, "enum": values}

    if is_dataclass(annotation):
        name = annotation.__name__
        if name in schemas:
            return {"$ref": f"#/components/schemas/{name}"}
        schemas[name] = {}
        try:
            field_hints = get_type_hints(annotation)
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
    try:
        type_hints = get_type_hints(route.view_func)
    except (NameError, TypeError):
        type_hints = {}
    description = metadata.pop("description", None) or inspect.getdoc(route.view_func) or ""
    summary = metadata.pop("summary", None)
    operation: dict[str, Any] = {
        "operationId": metadata.pop("operation_id", route.endpoint),
        "responses": metadata.pop(
            "responses",
            {
                "200": {
                    "description": "Successful response",
                    "content": {
                        "application/json": {
                            "schema": schema_for(
                                type_hints.get("return", signature.return_annotation),
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

    parameters = []
    for name, converter in route.converters.items():
        annotation = type_hints.get(
            name,
            signature.parameters.get(
                name, inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            ).annotation,
        )
        schema = schema_for(annotation, schemas) or _converter_schema(converter)
        parameters.append({"name": name, "in": "path", "required": True, "schema": schema})
    if parameters:
        operation["parameters"] = parameters

    request_body = metadata.pop("request_body", None)
    if request_body is not None and method not in {"GET", "HEAD"}:
        operation["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": schema_for(request_body, schemas)}},
        }
    operation.update(metadata.pop("openapi_extra", {}))
    return operation


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
