import pytest


def test_benchmark_statistics_and_validation():
    from bench.suite import percentile, validate

    assert percentile([3, 1, 2], 0.5) == 2
    assert percentile([], 0.95) is None
    assert validate(b'{"value":42}', {"value": 42})
    assert not validate(b'{"value":0}', {"value": 42})


def test_benchmark_real_server_smoke():
    pytest.importorskip("waitress")
    pytest.importorskip("starlette")
    psutil = pytest.importorskip("psutil")
    pytest.importorskip("uvicorn")
    pytest.importorskip("flask")
    from bench.suite import CASES, MODES, measure, server

    for mode in MODES:
        with server(mode) as (process, port):
            for name in ["json", "template"]:
                path, expected = CASES[name]
                result = measure(process, port, path, expected, concurrency=1, duration=0.05)
                assert result["measured_processes"] == 1 + len(
                    psutil.Process(process.pid).children(recursive=True)
                )
                assert (
                    result["successes"] > 0
                    and result["errors"] == 0
                    and result["invalid_responses"] == 0
                )
