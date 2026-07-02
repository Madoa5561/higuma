from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass


@dataclass
class Result:
    name: str
    ok: int
    elapsed: float

    @property
    def rps(self) -> float:
        if self.elapsed <= 0:
            return 0.0
        return self.ok / self.elapsed


def wait_ready(url: str, timeout: float = 10.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError(f"server not ready: {url}")


def run_load(url: str, concurrency: int, duration: float) -> Result:
    start = time.perf_counter()
    deadline = time.perf_counter() + duration

    def worker() -> int:
        ok = 0
        while time.perf_counter() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=1.0) as resp:
                    if resp.status == 200:
                        ok += 1
            except Exception:
                pass
        return ok

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        completed = sum(ex.map(lambda _: worker(), range(concurrency)))
    elapsed = time.perf_counter() - start
    return Result(name=url, ok=completed, elapsed=elapsed)


def benchmark(name: str, command: list[str], url: str, concurrency: int, duration: float) -> Result:
    proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_ready(url)
        result = run_load(url, concurrency, duration)
        return Result(name=name, ok=result.ok, elapsed=result.elapsed)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except Exception:
            proc.kill()


def main() -> None:
    duration = 8.0
    concurrency = 64

    higuma = benchmark(
        "higuma",
        [sys.executable, "bench/higuma_app.py"],
        "http://127.0.0.1:8010/ping",
        concurrency=concurrency,
        duration=duration,
    )
    flask = benchmark(
        "flask",
        [sys.executable, "bench/flask_app.py"],
        "http://127.0.0.1:8011/ping",
        concurrency=concurrency,
        duration=duration,
    )

    print(f"higuma: {higuma.rps:.1f} req/s (ok={higuma.ok}, {higuma.elapsed:.2f}s)")
    print(f"flask : {flask.rps:.1f} req/s (ok={flask.ok}, {flask.elapsed:.2f}s)")

    if flask.rps > 0:
        print(f"speedup: {higuma.rps / flask.rps:.2f}x")
    else:
        print("speedup: n/a")

    print("note: this is a local rough benchmark, not a formal benchmark.")


if __name__ == "__main__":
    main()
