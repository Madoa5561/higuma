# Performance measurements and tradeoffs

Rust is not a promise of universally higher speed. HTTP transport, Python work,
templates, I/O waits, and concurrency change results. Flask-style ergonomics and
integrated features are evaluated separately from throughput.

## Reproduce

### 0.10 candidate: async path on/off

The same 0.10.0 candidate async JSON/20ms-sleep routes were measured with only the fast-path setting
changed. Starlette uses the same Uvicorn/asyncio/h11. Windows/Python/hardware matches the environment
below. Three seeded-order rounds use 1.5 seconds after 0.3-second warmup. All 54 cases and warmups
had zero communication errors and invalid responses.
[Raw rounds, latency, CPU and RSS](../assets/benchmarks/asgi-async-windows-2026-10-27.json).
Source SHA-256: `fc10515fb4b63c6ba63b11cdbf97e88014c987cb82372d29de1dc4ef5d2679d1`.

Median JSON valid requests/second:

| Mode | c=1 | c=16 | c=64 | c=16 p95 (ms) |
| --- | ---: | ---: | ---: | ---: |
| Higuma bridge (OFF) | 1,298 | 1,699 | 1,685 | 10.41 |
| Higuma fast (ON) | 3,281 | 4,146 | 4,408 | 4.24 |
| Starlette | 5,568 | 7,704 | 7,553 | 2.37 |

The selected async JSON route improved about 2.44x at c=16, with better p95, but still achieved only
about 54% of Starlette's throughput. Routes using synchronous middleware/hooks/DI fall back and may
not benefit. This differs from the synchronous JSON workload in the historical 0.9 table.

Simulated 20ms I/O at c=64 measured 1,357 bridge, 1,495 fast and 1,629 Starlette req/s.
The improvement is smaller and round ranges overlap. It does not predict real database/network speedups.
The ASGI concurrency cap of 64 remains in both paths. Native/WSGI 0.10 was not remeasured here.

```bash
python -m bench.async_paths --output target/asgi-async-new.json --rounds 3 --duration 1.5 --concurrency 1 16 64
```

### Standard five-mode comparison

```bash
python -m pip install -e ".[benchmark,asgi,compat]"
python -m bench.suite --output target/benchmark-new.json --rounds 3 --duration 1.5 --concurrency 1 16
```

Output files are never overwritten. The older development-server comparison in
`bench/compare.py` is a rough tool, not the basis for performance claims.

Compare native Higuma, Higuma/Uvicorn, Higuma/Waitress, Flask/Waitress, and
Starlette/Uvicorn. All use one server process and HTTP/1.1 keep-alive. Uvicorn uses
asyncio/h11; Waitress has 16 threads and a 512-connection limit; native has one Tokio I/O worker; Higuma ASGI
has at most 64 coordination workers. One process does not imply identical internal
parallelism.

Five GET workloads cover small JSON, typed paths/query, simulated 20ms I/O,
Python CPU work, and 20-item HTML. Each concurrency 1/16 case warms up then runs
three rounds. An 8MiB upload case sends only five requests per mode/round to bound
disk work. Flask/Starlette also consume input incrementally rather than being
artificially forced to materialize bodies.

The harness validates status/JSON values/HTML elements/upload size and records
errors and invalid responses separately. JSON output includes raw rounds,
median/min/max throughput, p50/p95/p99, server/client CPU, sampled baseline/peak
RSS, dependency/Python/OS/CPU/RAM metadata, commit/dirty state, and a source hash.

## Historical Windows observations (0.9 candidate, 2026-10-27)

These are historical 0.9 candidate results, not 0.10 measurements. That implementation is included in commit `62e04a5`.

Windows 11 build 26200, CPython 3.12.9, 20 physical / 28 logical CPUs, about 31.7GiB RAM.
The release-built Higuma 0.9.0 candidate was compared with Flask 3.1.3,
Starlette 1.10.0, Uvicorn 0.54.0 and Waitress 3.0.2. Starlette's body limit was also
configured to 32MiB. This measures uncommitted candidate changes based on `9a8e1d0`.
The measured source SHA-256 is `fa06251c306c4ea88cb9013e871a0296ff3f6f12d263f4f778974ce626848281`.
[Raw results](../assets/benchmarks/windows-2026-10-27.json) include every round,
concurrency 1, versions and hardware metadata.

Median valid requests/second at concurrency 16, three 1.5-second rounds:

| Mode | JSON | path/query | Python CPU | HTML template |
| --- | ---: | ---: | ---: | ---: |
| Higuma native | 11,692 | 11,374 | 1,749 | 11,801 |
| Higuma / Uvicorn | 1,908 | 1,832 | 962 | 1,863 |
| Higuma / Waitress | 5,279 | 4,789 | 1,460 | 4,928 |
| Flask / Waitress | 5,983 | 5,510 | 1,481 | 4,988 |
| Starlette / Uvicorn | 7,549 | 7,187 | 1,571 | 6,478 |

For JSON/concurrency 16, p95 and CPU are round medians; RSS is the maximum sampled
peak for that case. CPU 1.0 means approximately one core, not 100% of this machine.

| Mode | p95 (ms) | RSS peak (MiB) | server CPU (core equivalents) |
| --- | ---: | ---: | ---: |
| Higuma native | 2.75 | 36.00 | 1.38 |
| Higuma / Uvicorn | 9.31 | 51.89 | 1.08 |
| Higuma / Waitress | 5.03 | 40.06 | 1.18 |
| Flask / Waitress | 4.48 | 46.17 | 1.13 |
| Starlette / Uvicorn | 2.48 | 41.98 | 1.00 |

Native JSON throughput was about 1.55 times Starlette's in this setup, but
Starlette had better p95 and CPU consumption. Higuma ASGI achieved about 25% of
Starlette's JSON throughput: the bridge/worker path still has a cost. Deploying
Flask-style handlers through ASGI does not promise ASGI-native efficiency.
Higuma WSGI also did not outperform Flask on this JSON workload. Python CPU work
is not automatically translated into Rust.

The 8MiB upload check uses only 15 requests per mode (five per round), not a
long-running or concurrent upload endurance test. Maximum observed RSS growth was
1.46MiB for native, 2.34MiB for Higuma ASGI, 0.79MiB for Higuma WSGI, 0.27MiB for
Flask and 0.63MiB for Starlette. This supports stored-body incremental reading;
allocator reuse, prior workloads and sampling prevent precise allocation attribution.

### I/O waits and concurrency

A separate three-round run measures the same simulated 20ms I/O at concurrency
16/64/128. [Raw I/O results](../assets/benchmarks/io-scaling-windows-2026-10-27.json):

| Mode | c=16 (req/s) | c=64 | c=128 |
| --- | ---: | ---: | ---: |
| Higuma native | 511 | 1,668 | 2,861 |
| Higuma / Uvicorn | 415 | 1,177 | 1,151 |
| Higuma / Waitress | 474 | 509 | 474 |
| Flask / Waitress | 748 | 754 | 740 |
| Starlette / Uvicorn | 539 | 1,663 | 2,873 |

Native and Starlette were close at c=128 in this run. Higuma ASGI's coordination
worker limit of 64 contributes to its plateau beyond c=64. Sixteen-thread WSGI
configurations saturate while waiting. Flask's effective sleep differs as noted
below. This is not evidence that increasing real database connections is safe.
All 165 main and 45 I/O measurement cases had zero communication errors and zero
invalid responses, including warmups.

Run this separately from the main benchmark:

```bash
python -m bench.suite --output target/io-scaling-new.json --rounds 3 --duration 1.5 --warmup 0.3 --concurrency 16 64 128 --cases io --upload-requests 0
```

## Interpretation limits

- Client and server share one machine: client CPU/GIL/socket overhead can cap results.
- Loopback results exclude TLS, proxy, real DB/network latency, and multiple workers.
- A 20ms sleep is simulated I/O, not database or resilience verification.
- RSS/CPU includes the complete server process tree, including the Windows venv
  launcher and actual server child. RSS is sampled every 20ms, not allocation attribution or exact transient peaks.
- Three rounds describe observations; they do not establish statistical significance or universal rankings.
- Higuma fully receives/spools uploads before dispatch. Waitress also buffers WSGI
  ingress, so Flask's stream is not necessarily live network input. Starlette/Uvicorn
  consumes live ASGI chunks and can begin processing earlier.
- ASGI support does not make Higuma coroutine-only internally.
- Windows timer resolution affects this workload. A separate probe on this host
  observed medians of about 31.1ms for `asyncio.sleep(0.02)` and 20.3ms for
  `time.sleep(0.02)`. Do not interpret Flask's synchronous sleep and the other
  modes' async sleeps as identical effective waits. See the
  [Python Windows asyncio notes](https://docs.python.org/3/library/asyncio-platforms.html#windows).

See also the ingress buffering described in the
[Waitress design](https://docs.pylonsproject.org/projects/waitress/en/stable/design.html).

The goal is to expose strengths and weaknesses under stated conditions, not
declare a winner. Rerun with your actual app, proxy, database, and deployment.
