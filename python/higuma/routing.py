from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote
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
        "int": r"-?\d+",
        "float": r"-?(?:\d+(?:\.\d*)?|\.\d+)",
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
    _regex: re.Pattern[str] = field(init=False, repr=False)
    _segments: list[tuple[str, str | None, str | None]] = field(
        default_factory=list, init=False, repr=False
    )
    specificity: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.rule = normalize_rule(self.rule)
        regex_parts: list[str] = []
        segments = [] if self.rule == "/" else self.rule.strip("/").split("/")

        for index, segment in enumerate(segments):
            match = _PARAMETER_RE.fullmatch(segment)
            if not match:
                if "<" in segment or ">" in segment:
                    raise ValueError(f"invalid route segment: {segment}")
                regex_parts.append(re.escape(segment))
                self._segments.append(("static", segment, None))
                self.specificity += 100
                continue

            converter = match.group("converter") or "string"
            name = match.group("name")
            if converter == "path" and index + 1 != len(segments):
                raise ValueError("<path:...> must be the final route segment")
            regex_parts.append(f"(?P<{name}>{_converter_pattern(converter)})")
            self._segments.append(("parameter", name, converter))
            self.converters[name] = converter
            self.specificity += 1 if converter == "path" else 20

        pattern = "/" if not regex_parts else "/" + "/".join(regex_parts)
        self._regex = re.compile(f"^{pattern}/?$")

    def match(self, path: str) -> dict[str, Any] | None:
        match = self._regex.fullmatch(path)
        if not match:
            return None
        return {
            name: _convert_value(self.converters[name], value)
            for name, value in match.groupdict().items()
        }

    def convert_params(self, params: Mapping[str, str]) -> dict[str, Any]:
        return {
            name: _convert_value(self.converters.get(name, "string"), value)
            for name, value in params.items()
        }

    def build(self, values: Mapping[str, Any]) -> str:
        parts: list[str] = []
        for kind, value, converter in self._segments:
            if kind == "static":
                parts.append(str(value))
                continue
            if value is None:
                raise RuntimeError("route parameter name is missing")
            if value not in values:
                raise KeyError(f"missing URL value: {value}")
            safe = "/" if converter == "path" else ""
            parts.append(quote(str(values[value]), safe=safe))
        return "/" if not parts else "/" + "/".join(parts)
