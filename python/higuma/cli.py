from __future__ import annotations

import argparse
import importlib
import os
from typing import Any

from .app import Higuma, __version__


def load_app(reference: str) -> Higuma:
    module_name, separator, object_name = reference.partition(":")
    if not separator:
        object_name = "app"
    module = importlib.import_module(module_name)
    app_object: Any = getattr(module, object_name)
    if callable(app_object) and not isinstance(app_object, Higuma):
        app_object = app_object()
    if not isinstance(app_object, Higuma):
        raise TypeError(f"{reference!r} did not resolve to a Higuma application")
    return app_object


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="higuma")
    parser.add_argument("--version", action="version", version=f"higuma {__version__}")
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
    run_parser.add_argument("--debug", action="store_true")
    run_parser.add_argument("--internal-worker", action="store_true", help=argparse.SUPPRESS)

    routes_parser = subcommands.add_parser("routes", help="list application routes")
    routes_parser.add_argument("app", nargs="?", default=os.getenv("HIGUMA_APP", "app:app"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    app = load_app(args.app)

    if args.command == "run":
        app.run(
            host=args.host,
            port=args.port,
            workers=args.workers,
            processes=1 if args.internal_worker else args.processes,
            app_ref=args.app,
            debug=args.debug,
        )
        return 0

    if args.command == "routes":
        print(f"{'Endpoint':30} {'Methods':25} Rule")
        print(f"{'-' * 30} {'-' * 25} {'-' * 30}")
        for route in app._routes:
            print(f"{route.endpoint:30} {','.join(route.methods):25} {route.rule}")
        return 0

    return 1
