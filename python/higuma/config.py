from __future__ import annotations

import errno
import importlib
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


class Config(dict[str, Any]):
    def __init__(self, *args: Any, root_path: str | Path = ".", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        object.__setattr__(self, "_root_path", Path(root_path).resolve())

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
        source = obj
        if isinstance(obj, str):
            try:
                source = importlib.import_module(obj)
            except ModuleNotFoundError as error:
                if error.name != obj:
                    raise
                module, separator, attribute = obj.rpartition(".")
                if not separator:
                    raise
                source = getattr(importlib.import_module(module), attribute)
        for name in dir(source):
            if name.isupper():
                self[name] = getattr(source, name)
        return True

    def from_file(
        self,
        filename: str | Path,
        load: Callable[[Any], Any] = json.load,
        silent: bool = False,
        text: bool = True,
    ) -> bool:
        path = self._root_path / filename
        try:
            handle = path.open("r" if text else "rb", encoding="utf-8" if text else None)
        except OSError as error:
            if silent and error.errno in {errno.ENOENT, errno.EISDIR, errno.ENOTDIR}:
                return False
            raise
        with handle:
            values = load(handle)
        if not isinstance(values, Mapping):
            raise TypeError("configuration loader must return a mapping")
        self.update(values)
        return True

    def from_envvar(self, variable_name: str, silent: bool = False) -> bool:
        filename = os.getenv(variable_name)
        if not filename:
            if silent:
                return False
            raise RuntimeError(f"environment variable {variable_name!r} is not set")
        return self.from_file(filename, silent=silent)

    def from_prefixed_env(
        self, prefix: str = "HIGUMA", *, loads: Callable[[str], Any] = json.loads
    ) -> bool:
        marker = f"{prefix}_"
        for name in sorted(os.environ):
            if not name.startswith(marker):
                continue
            raw_value = os.environ[name]
            key = name[len(marker) :]
            try:
                value = loads(raw_value)
            except Exception:  # noqa: BLE001 - Flask-compatible loader fallback
                value = raw_value
            current = self
            *parts, tail = key.split("__")
            for part in parts:
                if part not in current:
                    current[part] = {}
                current = current[part]
                if not isinstance(current, dict):
                    raise TypeError("nested environment configuration requires dictionaries")
            current[tail] = value
        return True

    def get_namespace(
        self, namespace: str, lowercase: bool = True, trim_namespace: bool = True
    ) -> dict[str, Any]:
        result = {}
        for name, value in self.items():
            if not name.startswith(namespace):
                continue
            key = name[len(namespace) :] if trim_namespace else name
            result[key.lower() if lowercase else key] = value
        return result
