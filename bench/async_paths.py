"""Same-code async fast-path on/off comparison, not a replacement for the 0.9 baseline."""

import argparse
import json
import random
from pathlib import Path

from .suite import CASES, measure, metadata, server, summarize


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--duration", type=float, default=1.5)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 16, 64])
    args = parser.parse_args()
    if args.output.exists() or args.rounds < 1 or args.duration <= 0 or min(args.concurrency) < 1:
        parser.error("choose a new output and positive measurement settings")
    configs = [
        ("higuma-bridge", "higuma-asgi", ["--async-json", "--async-fast-path", "off"]),
        ("higuma-fast", "higuma-asgi", ["--async-json"]),
        ("starlette", "starlette-asgi", []),
    ]
    report = {
        "environment": metadata(),
        "settings": {
            "rounds": args.rounds,
            "duration": args.duration,
            "concurrency": args.concurrency,
            "warmup": 0.3,
            "seed": 20261027,
            "workload": "async JSON and async sleep(20ms); same Higuma code with fast path on/off; ASGI concurrency cap 64",
        },
        "results": [],
    }
    order = random.Random(20261027)
    for round_number in range(args.rounds):
        order.shuffle(configs)
        for label, mode, arguments in configs:
            with server(mode, arguments=arguments) as (process, port):
                for case in ("json", "io"):
                    path, expected = CASES[case]
                    for concurrency in args.concurrency:
                        warmup = measure(
                            process, port, path, expected, concurrency=concurrency, duration=0.3
                        )
                        result = measure(
                            process,
                            port,
                            path,
                            expected,
                            concurrency=concurrency,
                            duration=args.duration,
                        )
                        result.update(
                            mode=label,
                            case=case,
                            round=round_number + 1,
                            warmup_errors=warmup["errors"],
                            warmup_invalid_responses=warmup["invalid_responses"],
                        )
                        report["results"].append(result)
                        print(
                            label,
                            case,
                            concurrency,
                            round(result["valid_rps"], 1),
                            "errors",
                            result["errors"],
                            flush=True,
                        )
    report["summary"] = summarize(report["results"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if any(
        row["errors"]
        or row["invalid_responses"]
        or row["warmup_errors"]
        or row["warmup_invalid_responses"]
        for row in report["results"]
    ):
        raise SystemExit("Measurement failed; do not report this as error-free throughput")


if __name__ == "__main__":
    main()
