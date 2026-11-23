from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, unquote_to_bytes
from uuid import UUID

_PARAMETER_RE = re.compile(
    r"^<(?:(?P<converter>string|str|int|float|uuid|path):)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)>$"
)


def normalize_rule(rule: str) -> str:
    if not rule.startswith("/"):
        rule = f"/{rule}"
    if len(rule) > 1:
        rule = rule.rstrip("/")
    return rule or "/"


def _converter_pattern(name: str) -> str:
    return {
        "string": r"[^/]+",
        "str": r"[^/]+",
        "int": r"-?[0-9]+",
        "float": r"-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)",
        "uuid": r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        "path": r".+",
    }[name]


def _convert_value(converter: str, value: str) -> Any:
    if converter == "int":
        return int(value)
    if converter == "float":
        return float(value)
    if converter == "uuid":
        return UUID(value)
    return value


@dataclass
class Rule:
    rule: str
    methods: tuple[str, ...]
    endpoint: str
    view_func: Callable[..., Any]
    openapi: dict[str, Any] = field(default_factory=dict)
    include_in_schema: bool = True
    callback: Callable[[Mapping[str, Any]], Any] | None = None
    converters: dict[str, str] = field(default_factory=dict, init=False)
    _segments: list[tuple[str, str | None, str | None]] = field(
        default_factory=list, init=False, repr=False
    )
    specificity: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.rule = normalize_rule(self.rule)
        segments = [] if self.rule == "/" else self.rule.strip("/").split("/")

        for index, segment in enumerate(segments):
            match = _PARAMETER_RE.fullmatch(segment)
            if not match:
                if "<" in segment or ">" in segment:
                    raise ValueError(f"invalid route segment: {segment}")
                self._segments.append(("static", segment, None))
                self.specificity += 100
                continue

            converter = match.group("converter") or "string"
            name = match.group("name")
            if name in self.converters:
                raise ValueError(f"duplicate route parameter name: {name}")
            if converter == "path" and index + 1 != len(segments):
                raise ValueError("<path:...> must be the final route segment")
            self._segments.append(("parameter", name, converter))
            self.converters[name] = converter
            self.specificity += (
                1 if converter == "path" else 20 if converter in {"string", "str"} else 30
            )

    def match(self, path: str) -> dict[str, Any] | None:
        normalized = normalize_rule(path)
        parts = [] if normalized == "/" else normalized.removeprefix("/").split("/")
        parameters: dict[str, Any] = {}
        path_index = 0
        for kind, value, converter in self._segments:
            if kind == "static":
                if path_index >= len(parts):
                    return None
                decoded = _decode_segment(parts[path_index])
                if decoded is None or decoded != value:
                    return None
                path_index += 1
                continue

            if value is None or converter is None or path_index >= len(parts):
                return None
            if converter == "path":
                decoded_parts = [_decode_segment(part) for part in parts[path_index:]]
                if any(part is None for part in decoded_parts):
                    return None
                decoded = "/".join(str(part) for part in decoded_parts)
                if any(_is_control(character) for character in decoded):
                    return None
                parameters[value] = decoded
                path_index = len(parts)
                continue

            decoded = _decode_segment(parts[path_index])
            if (
                decoded is None
                or "/" in decoded
                or "\\" in decoded
                or any(_is_control(character) for character in decoded)
                or re.fullmatch(_converter_pattern(converter), decoded) is None
            ):
                return None
            try:
                converted = _convert_value(converter, decoded)
            except (ValueError, OverflowError):
                return None
            if converter == "float" and not math.isfinite(converted):
                return None
            parameters[value] = converted
            path_index += 1

        return parameters if path_index == len(parts) else None

    def convert_params(self, params: Mapping[str, str]) -> dict[str, Any]:
        return {
            name: _convert_value(self.converters.get(name, "string"), value)
            for name, value in params.items()
        }

    def build(self, values: Mapping[str, Any]) -> str:
        parts: list[str] = []
        for kind, value, converter in self._segments:
            if kind == "static":
                parts.append(quote(str(value), safe="!$&'()*+,-.:;=@_~"))
                continue
            if value is None:
                raise RuntimeError("route parameter name is missing")
            if value not in values:
                raise KeyError(f"missing URL value: {value}")
            raw = values[value]
            if raw is None:
                raise ValueError(f"URL parameter {value!r} cannot be None")
            text = _format_url_value(converter, value, raw)
            safe = "/" if converter == "path" else ""
            parts.append(quote(text, safe=safe))
        return "/" if not parts else "/" + "/".join(parts)


def _format_url_value(converter: str | None, name: str, raw: Any) -> str:
    if converter == "int":
        if isinstance(raw, bool):
            raise ValueError(f"URL parameter {name!r} must be an integer")
        if isinstance(raw, int):
            return str(int(raw))
        if isinstance(raw, str):
            return str(int(raw))
        raise ValueError(f"URL parameter {name!r} must be an integer")
    if converter == "float":
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            raise ValueError(f"URL parameter {name!r} must be a finite float")
        number = float(raw)
        if not math.isfinite(number):
            raise ValueError(f"URL parameter {name!r} must be a finite float")
        return str(number)
    if converter == "uuid":
        return str(raw if isinstance(raw, UUID) else UUID(str(raw)))
    return str(raw)


def _decode_segment(value: str) -> str | None:
    try:
        return unquote_to_bytes(value).decode("utf-8")
    except UnicodeDecodeError:
        return None


def _is_control(value: str) -> bool:
    return ord(value) < 32 or ord(value) == 127
