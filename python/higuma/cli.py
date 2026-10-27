from __future__ import annotations

import argparse
import code
import importlib
import os
from typing import Any

from .app import Higuma, __version__
from .commands import invoke_command


def load_app(reference: str) -> Higuma:
    module_name, separator, object_name = reference.partition(":")
    if not separator:
        object_name = "app"
    object_name = object_name.removesuffix("()")
    module = importlib.import_module(module_name)
    app_object: Any = getattr(module, object_name)
    if callable(app_object) and not isinstance(app_object, Higuma):
        app_object = app_object()
    if not isinstance(app_object, Higuma):
        raise TypeError(f"{reference!r} did not resolve to a Higuma application")
    return app_object


def build_parser(app: Higuma | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="higuma")
    parser.add_argument("--version", action="version", version=f"higuma {__version__}")
    parser.add_argument("--app", dest="app_reference", help="application module:object or factory")
    subcommands = parser.add_subparsers(dest="command", required=True)

    run_parser = subcommands.add_parser("run", help="run a Higuma application")
    run_parser.add_argument("app", nargs="?", default=os.getenv("HIGUMA_APP", "app:app"))
    run_parser.add_argument("--host", default="127.0.0.1")
    run_parser.add_argument("--port", type=int, default=8000)
    run_parser.add_argument("--workers", type=int, default=0)
    run_parser.add_argument(
        "--processes",
        type=int,
        default=1,
        help="number of isolated worker processes",
    )
    run_parser.add_argument(
        "--max-connections",
        type=int,
        default=1024,
        help="maximum concurrent supervisor connections",
    )
    run_parser.add_argument("--max-restarts", type=int, default=5)
    run_parser.add_argument("--restart-window", type=float, default=60.0)
    run_parser.add_argument("--debug", action="store_true")
    run_parser.add_argument("--internal-worker", action="store_true", help=argparse.SUPPRESS)

    routes_parser = subcommands.add_parser("routes", help="list application routes")
    routes_parser.add_argument("app", nargs="?", default=os.getenv("HIGUMA_APP", "app:app"))
    shell_parser = subcommands.add_parser("shell", help="open a shell in the app context")
    shell_parser.add_argument("app", nargs="?", default=os.getenv("HIGUMA_APP", "app:app"))
    if app is not None:
        app.cli.add_to(subcommands)
        groups = {}
        for name, blueprint in app._blueprints.items():
            if not blueprint.cli.commands:
                continue
            current = subcommands
            prefix = ""
            for part in name.split("."):
                prefix = (prefix + "." + part).lstrip(".")
                if prefix not in groups:
                    group_parser = current.add_parser(part, help=f"{prefix} blueprint commands")
                    groups[prefix] = group_parser.add_subparsers(
                        dest="_group_" + prefix, required=True
                    )
                current = groups[prefix]
            blueprint.cli.add_to(current)
    return parser


def main(argv: list[str] | None = None) -> int:
    discovery = argparse.ArgumentParser(add_help=False)
    discovery.add_argument("--app", dest="app_reference")
    known, rest = discovery.parse_known_args(argv)
    if known.app_reference:
        app = load_app(known.app_reference)
        return execute(app, build_parser(app).parse_args(argv))
    if rest and rest[0] not in {"run", "routes", "shell", "--help", "-h", "--version"}:
        app = load_app(os.getenv("HIGUMA_APP", "app:app"))
        return execute(app, build_parser(app).parse_args(argv))
    args = build_parser().parse_args(argv)
    return execute(load_app(args.app), args)


def execute(app: Higuma, args: argparse.Namespace) -> int:
    if hasattr(args, "_higuma_command"):
        return invoke_command(app, args)

    if args.command == "run":
        app.run(
            host=args.host,
            port=args.port,
            workers=args.workers,
            processes=1 if args.internal_worker else args.processes,
            app_ref=args.app_reference or args.app,
            debug=args.debug,
            max_connections=args.max_connections,
            max_restarts=args.max_restarts,
            restart_window=args.restart_window,
        )
        return 0

    if args.command == "routes":
        print(f"{'Endpoint':30} {'Methods':25} Rule")
        print(f"{'-' * 30} {'-' * 25} {'-' * 30}")
        for route in app._routes:
            print(f"{route.endpoint:30} {','.join(route.methods):25} {route.rule}")
        for route in app._websocket_rules.values():
            print(f"{route.endpoint:30} {'WEBSOCKET':25} {route.rule}")
        return 0

    if args.command == "shell":
        with app.app_context():
            code.interact(local={"app": app}, banner="Higuma application shell")
        return 0

    return 1
