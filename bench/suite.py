"""Reproducible loopback comparison. Results are observations, not universal speed claims."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import importlib.metadata
import json
import platform
import random
import socket
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryFile

from .apps import CPU_VALUE, JSON_VALUE

MODES = ["higuma-native", "higuma-asgi", "higuma-wsgi", "flask-wsgi", "starlette-asgi"]
CASES = {
    "json": ("/json", JSON_VALUE),
    "params": ("/params/42?name=bear", {"id": 42, "name": "bear"}),
    "io": ("/io", JSON_VALUE),
    "cpu": ("/cpu", {"value": CPU_VALUE}),
    "template": ("/template", None),
}


def percentile(values, percent):
    ordered = sorted(values)
    return (
        ordered[min(len(ordered) - 1, max(0, int((len(ordered) - 1) * percent)))]
        if ordered
        else None
    )


def validate(body, expected):
    if expected is None:
        return b"Hello bear" in body and body.count(b"<li>") == 20
    return json.loads(body) == expected


@contextmanager
def server(mode):
    import psutil

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with TemporaryFile(mode="w+t", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "bench.apps", "--mode", mode, "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    log.seek(0)
                    raise RuntimeError(f"{mode} exited: {log.read()[-8000:]}")
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
                try:
                    connection.request("GET", "/json")
                    response = connection.getresponse()
                    if response.status == 200 and validate(response.read(), JSON_VALUE):
                        break
                except (OSError, http.client.HTTPException):
                    pass
                finally:
                    connection.close()
                time.sleep(0.05)
            else:
                log.seek(0)
                raise TimeoutError(f"{mode} did not start: {log.read()[-8000:]}")
            process.benchmark_log = log
            yield process, port
        finally:
            try:
                children = psutil.Process(process.pid).children(recursive=True)
            except psutil.NoSuchProcess:
                children = []
            for child in reversed(children):
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            _, alive = psutil.wait_procs(children, timeout=3)
            for child in alive:
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def measure(process, port, path, expected, *, concurrency, duration, upload_requests=0):
    import psutil

    root = psutil.Process(process.pid)
    monitored = [root, *root.children(recursive=True)]

    def memory():
        return sum(item.memory_info().rss for item in monitored)

    def cpu_seconds():
        return sum(item.cpu_times().user + item.cpu_times().system for item in monitored)

    barrier = threading.Barrier(concurrency + 1)
    stop = threading.Event()
    deadline = [0.0]
    peak = [memory()]
    baseline = peak[0]

    def sample():
        while not stop.wait(0.02):
            try:
                peak[0] = max(peak[0], memory())
            except psutil.NoSuchProcess:
                return

    payload = b"x" * (8 * 1024 * 1024) if upload_requests else None

    def worker():
        latencies, errors, invalid, successes, transferred = [], 0, 0, 0, 0
        error_kinds = {}
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        barrier.wait()
        while (
            len(latencies) < upload_requests
            if upload_requests
            else time.perf_counter() < deadline[0]
        ):
            started = time.perf_counter()
            try:
                connection.request("POST" if upload_requests else "GET", path, body=payload)
                response = connection.getresponse()
                body = response.read()
                if response.status != 200:
                    errors += 1
                    key = "HTTP " + str(response.status)
                    error_kinds[key] = error_kinds.get(key, 0) + 1
                elif not validate(body, {"size": len(payload)} if upload_requests else expected):
                    invalid += 1
                else:
                    successes += 1
                    transferred += len(body)
            except (OSError, ValueError, http.client.HTTPException) as error:
                errors += 1
                key = type(error).__name__ + ": " + str(error)
                error_kinds[key] = error_kinds.get(key, 0) + 1
                connection.close()
                time.sleep(0.01)
            latencies.append((time.perf_counter() - started) * 1000)
        connection.close()
        return latencies, errors, invalid, successes, transferred, error_kinds

    before = cpu_seconds()
    client_before = time.process_time()
    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            tasks = [pool.submit(worker) for _ in range(concurrency)]
            started = time.perf_counter()
            deadline[0] = started + duration
            barrier.wait()
            results = [task.result() for task in tasks]
            elapsed = time.perf_counter() - started
        after = cpu_seconds()
        peak[0] = max(peak[0], memory())
        error_kinds = {}
        for item in results:
            for key, count in item[5].items():
                error_kinds[key] = error_kinds.get(key, 0) + count
        latencies = [value for item in results for value in item[0]]
        successes = sum(item[3] for item in results)
        return {
            "concurrency": concurrency,
            "elapsed_seconds": elapsed,
            "requests": len(latencies),
            "successes": successes,
            "errors": sum(item[1] for item in results),
            "invalid_responses": sum(item[2] for item in results),
            "error_kinds": error_kinds,
            "measured_processes": len(monitored),
            "valid_rps": successes / elapsed,
            "latency_ms": {
                "p50": percentile(latencies, 0.50),
                "p95": percentile(latencies, 0.95),
                "p99": percentile(latencies, 0.99),
            },
            "rss_baseline_bytes": baseline,
            "rss_peak_bytes": peak[0],
            "rss_growth_bytes": peak[0] - baseline,
            "server_cpu_cores": (after - before) / elapsed,
            "client_cpu_cores": (time.process_time() - client_before) / elapsed,
            "response_bytes": sum(item[4] for item in results),
        }
    finally:
        stop.set()
        sampler.join(timeout=2)


def summarize(results):
    groups = {}
    for item in results:
        groups.setdefault((item["mode"], item["case"], item["concurrency"]), []).append(item)
    return [
        {
            "mode": mode,
            "case": case,
            "concurrency": concurrency,
            "rounds": len(rows),
            "median_valid_rps": statistics.median(row["valid_rps"] for row in rows),
            "min_valid_rps": min(row["valid_rps"] for row in rows),
            "max_valid_rps": max(row["valid_rps"] for row in rows),
            "median_p95_ms": statistics.median(row["latency_ms"]["p95"] for row in rows),
            "max_rss_bytes": max(row["rss_peak_bytes"] for row in rows),
            "errors": sum(row["errors"] for row in rows),
            "invalid_responses": sum(row["invalid_responses"] for row in rows),
        }
        for (mode, case, concurrency), rows in sorted(groups.items())
    ]


def metadata():
    import psutil

    digest = hashlib.sha256()
    for path in sorted(
        [
            *Path("python/higuma").glob("*.py"),
            Path("src/lib.rs"),
            Path("Cargo.lock"),
            *Path("bench").glob("*.py"),
        ]
    ):
        digest.update(path.as_posix().encode())
        digest.update(path.read_bytes())
    return {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version,
        "cpu": platform.processor(),
        "logical_cpus": psutil.cpu_count(),
        "physical_cpus": psutil.cpu_count(logical=False),
        "ram_bytes": psutil.virtual_memory().total,
        "versions": {
            name: importlib.metadata.version(name)
            for name in ["higuma", "flask", "starlette", "uvicorn", "waitress", "psutil"]
        },
        "source_sha256": digest.hexdigest(),
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True)),
        "transport": "loopback HTTP/1.1, keep-alive, one server process tree; Uvicorn asyncio/h11, Waitress 16 threads/512 connections, native Tokio 1 I/O worker; Higuma ASGI coordination 64",
        "caveats": [
            "Same Windows host generates load and serves; CPU/GIL/client can limit throughput.",
            "No TLS/proxy/database/network RTT; 20ms I/O is simulated, not database performance.",
            "RSS/CPU includes the server process tree (including Windows venv launchers), sampled every 20ms, not allocation attribution.",
            "Higuma spools uploads before dispatch; Waitress also buffers WSGI ingress; Starlette/Uvicorn consumes live ASGI chunks.",
            "No claim of statistical significance or universal framework ranking.",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--duration", type=float, default=1.5)
    parser.add_argument("--warmup", type=float, default=0.3)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 16])
    parser.add_argument("--modes", nargs="+", choices=MODES, default=MODES)
    parser.add_argument("--cases", nargs="+", choices=list(CASES), default=list(CASES))
    parser.add_argument("--upload-requests", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20261027)
    parser.add_argument("--build-label", default="unspecified")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists; choose a new filename")
    if (
        args.rounds < 1
        or args.duration <= 0
        or args.warmup <= 0
        or min(args.concurrency) < 1
        or args.upload_requests < 0
    ):
        parser.error("rounds, duration, warmup, and concurrency must be positive")
    report = {
        "environment": metadata(),
        "settings": {key: value for key, value in vars(args).items() if key != "output"},
        "results": [],
    }
    order = random.Random(args.seed)
    for round_number in range(args.rounds):
        modes = list(args.modes)
        order.shuffle(modes)
        for mode in modes:
            with server(mode) as (process, port):
                for case in args.cases:
                    path, expected = CASES[case]
                    for concurrency in args.concurrency:
                        warmup = measure(
                            process,
                            port,
                            path,
                            expected,
                            concurrency=concurrency,
                            duration=args.warmup,
                        )
                        result = measure(
                            process,
                            port,
                            path,
                            expected,
                            concurrency=concurrency,
                            duration=args.duration,
                        )
                        result.update(mode=mode, case=case, round=round_number + 1)
                        result.update(
                            warmup_errors=warmup["errors"],
                            warmup_invalid_responses=warmup["invalid_responses"],
                        )
                        if result["errors"] or result["invalid_responses"]:
                            process.benchmark_log.flush()
                            process.benchmark_log.seek(0)
                            result["server_log_excerpt"] = process.benchmark_log.read()[-8000:]
                        report["results"].append(result)
                        print(
                            f"{round_number + 1} {mode:16} {case:8} c={concurrency:2} {result['valid_rps']:8.1f} req/s errors={result['errors']} invalid={result['invalid_responses']}",
                            flush=True,
                        )
                if args.upload_requests:
                    result = measure(
                        process,
                        port,
                        "/upload",
                        None,
                        concurrency=1,
                        duration=args.duration,
                        upload_requests=args.upload_requests,
                    )
                    result.update(mode=mode, case="upload-8MiB", round=round_number + 1)
                    report["results"].append(result)
    report["summary"] = summarize(report["results"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if any(
        item["errors"]
        or item["invalid_responses"]
        or item.get("warmup_errors")
        or item.get("warmup_invalid_responses")
        for item in report["results"]
    ):
        raise SystemExit(
            "Benchmark had failures; do not present throughput as an error-free result"
        )


if __name__ == "__main__":
    main()
