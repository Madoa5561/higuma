from __future__ import annotations

import argparse
import inspect
import io
import shlex
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from typing import Any, get_type_hints

from .execution import resolve_awaitable


class CommandGroup:
    """Small argparse-backed registry, not a Click compatibility layer."""

    def __init__(self) -> None:
        self.commands: dict[str, tuple[Callable[..., Any], str]] = {}

    def command(self, name: str | None = None, *, help: str | None = None):
        if name is not None and not isinstance(name, str):
            raise ValueError("command names must contain letters, digits, or hyphens")
        if help is not None and not isinstance(help, str):
            raise TypeError("command help must be a string")

        def register(function: Callable[..., Any]):
            if not callable(function):
                raise TypeError("CLI command must be callable")
            command_name = function.__name__.replace("_", "-") if name is None else name
            if (
                not command_name
                or not command_name[0].isalnum()
                or not all(char.isalnum() or char == "-" for char in command_name)
            ):
                raise ValueError("command names must contain letters, digits, or hyphens")
            if command_name in {"run", "routes", "shell"} or command_name in self.commands:
                raise ValueError(f"reserved or duplicate command: {command_name}")
            frame = inspect.currentframe()
            try:
                localns = (
                    dict(frame.f_back.f_locals)
                    if frame is not None and frame.f_back is not None
                    else {}
                )
            finally:
                del frame
            hints = _command_hints(function, localns)
            try:
                function.__higuma_cli_hints__ = hints
            except (AttributeError, TypeError):
                pass
            _arguments(argparse.ArgumentParser(add_help=False), function, hints)
            self.commands[command_name] = (function, help or inspect.getdoc(function) or "")
            return function

        return register

    def add_to(self, subcommands: Any) -> None:
        for name, (function, description) in self.commands.items():
            parser = subcommands.add_parser(name, help=description, description=description)
            _arguments(parser, function, getattr(function, "__higuma_cli_hints__", None))
            parser.set_defaults(_higuma_command=function)


def _command_hints(function: Callable[..., Any], localns: dict[str, Any] | None) -> dict[str, Any]:
    globalns = getattr(function, "__globals__", None)
    try:
        return get_type_hints(function, globalns=globalns, localns=localns)
    except (NameError, TypeError):
        resolved: dict[str, Any] = {}
        namespace = {**dict(globalns or {}), **dict(localns or {})}
        for name, parameter in inspect.signature(function).parameters.items():
            annotation = parameter.annotation
            if isinstance(annotation, str):
                candidate = {
                    "str": str,
                    "int": int,
                    "float": float,
                    "bool": bool,
                }.get(annotation.strip()) or namespace.get(annotation.strip())
                if candidate is not None:
                    resolved[name] = candidate
            elif annotation is not inspect.Parameter.empty:
                resolved[name] = annotation
        return resolved


def _arguments(
    parser: argparse.ArgumentParser,
    function: Callable[..., Any],
    hints: dict[str, Any] | None = None,
) -> None:
    hints = _command_hints(function, None) if hints is None else hints
    for name, parameter in inspect.signature(function).parameters.items():
        if name.startswith("_") or name in {"command", "app_reference"}:
            raise ValueError("CLI parameter name conflicts with command parser internals")
        if parameter.kind not in {parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY}:
            raise TypeError("CLI arguments must be named positional or keyword-only parameters")
        value_type = hints.get(name, str)
        if value_type not in {str, int, float, bool}:
            raise TypeError("CLI parameter annotations must be str, int, float, or bool")
        required = parameter.default is parameter.empty
        if value_type is bool:
            if required or not isinstance(parameter.default, bool):
                raise TypeError("boolean CLI flags must have a bool default")
            parser.add_argument(
                "--" + name.replace("_", "-"),
                dest=name,
                default=parameter.default,
                action="store_false" if parameter.default else "store_true",
            )
        elif required and parameter.kind is parameter.POSITIONAL_OR_KEYWORD:
            parser.add_argument(name, type=value_type)
        else:
            options = {"type": value_type, "dest": name, "required": required}
            if not required:
                options["default"] = parameter.default
            parser.add_argument("--" + name.replace("_", "-"), **options)


def invoke_command(app: Any, args: argparse.Namespace) -> int:
    function = args._higuma_command
    values = {name: getattr(args, name) for name in inspect.signature(function).parameters}
    with app.app_context():
        result = resolve_awaitable(function(**values))
    if result is not None:
        print(result)
    return 0


@dataclass
class CliResult:
    exit_code: int
    output: str
    exception: BaseException | None = None


class CliRunner:
    def __init__(self, app: Any) -> None:
        self.app = app

    def invoke(self, args: list[str] | tuple[str, ...] | str = ()) -> CliResult:
        from .cli import build_parser, execute

        output = io.StringIO()
        error = None
        code = 0
        with redirect_stdout(output), redirect_stderr(output):
            try:
                if isinstance(args, (bytes, bytearray)):
                    raise TypeError("args must be a command string or sequence of strings")
                if isinstance(args, str):
                    parsed_args = shlex.split(args)
                else:
                    parsed_args = list(args)
                    if not all(isinstance(item, str) for item in parsed_args):
                        raise TypeError("args must be a command string or sequence of strings")
                code = execute(self.app, build_parser(self.app).parse_args(parsed_args))
            except SystemExit as exception:
                if exception.code is None or isinstance(exception.code, int):
                    code = int(exception.code or 0)
                else:
                    code = 1
                    print(exception.code)
            except Exception as exception:  # noqa: BLE001 - expose command failures to tests
                error, code = exception, 1
        return CliResult(code, output.getvalue(), error)
