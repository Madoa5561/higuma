from __future__ import annotations

import argparse
import inspect
import io
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
        def register(function: Callable[..., Any]):
            command_name = name or function.__name__.replace("_", "-")
            if (
                not command_name
                or not command_name[0].isalnum()
                or not all(char.isalnum() or char == "-" for char in command_name)
            ):
                raise ValueError("command names must contain letters, digits, or hyphens")
            if command_name in {"run", "routes", "shell"} or command_name in self.commands:
                raise ValueError(f"reserved or duplicate command: {command_name}")
            _arguments(argparse.ArgumentParser(add_help=False), function)
            self.commands[command_name] = (function, help or inspect.getdoc(function) or "")
            return function

        return register

    def add_to(self, subcommands: Any) -> None:
        for name, (function, description) in self.commands.items():
            parser = subcommands.add_parser(name, help=description, description=description)
            _arguments(parser, function)
            parser.set_defaults(_higuma_command=function)


def _arguments(parser: argparse.ArgumentParser, function: Callable[..., Any]) -> None:
    hints = get_type_hints(function)
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
            if required:
                raise TypeError("boolean CLI flags must have a default")
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

    def invoke(self, args: list[str] | tuple[str, ...] = ()) -> CliResult:
        from .cli import build_parser, execute

        output = io.StringIO()
        error = None
        code = 0
        with redirect_stdout(output), redirect_stderr(output):
            try:
                code = execute(self.app, build_parser(self.app).parse_args(list(args)))
            except SystemExit as exception:
                if exception.code is None or isinstance(exception.code, int):
                    code = int(exception.code or 0)
                else:
                    code = 1
                    print(exception.code)
            except Exception as exception:  # noqa: BLE001 - expose command failures to tests
                error, code = exception, 1
        return CliResult(code, output.getvalue(), error)
