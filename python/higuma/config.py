from __future__ import annotations

import importlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class Config(dict[str, Any]):
    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value

    def from_mapping(
        self,
        mapping: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> bool:
        if mapping:
            self.update(mapping)
        self.update(kwargs)
        return True

    def from_object(self, obj: object | str) -> bool:
        source = importlib.import_module(obj) if isinstance(obj, str) else obj
        for name in dir(source):
            if name.isupper():
                self[name] = getattr(source, name)
        return True

    def from_file(self, filename: str | Path) -> bool:
        path = Path(filename)
        with path.open("r", encoding="utf-8") as handle:
            values = json.load(handle)
        if not isinstance(values, dict):
            raise TypeError("configuration file must contain a JSON object")
        self.update(values)
        return True

    def from_envvar(self, variable_name: str, silent: bool = False) -> bool:
        filename = os.getenv(variable_name)
        if not filename:
            if silent:
                return False
            raise RuntimeError(f"environment variable {variable_name!r} is not set")
        return self.from_file(filename)

    def from_prefixed_env(self, prefix: str = "HIGUMA") -> bool:
        marker = f"{prefix}_"
        for name, raw_value in os.environ.items():
            if not name.startswith(marker):
                continue
            key = name[len(marker) :]
            try:
                value = json.loads(raw_value)
            except json.JSONDecodeError:
                value = raw_value
            self[key] = value
        return True
