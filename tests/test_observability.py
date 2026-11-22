import asyncio
import io
import json
import logging
import threading

import pytest
from higuma import HealthChecks, Higuma, JSONLogFormatter, Observability, request


def test_observability_asgi_ids_metrics_and_secret_free_structured_logging():
    from test_gateways import asgi_request

    output = io.StringIO()
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(output)
    handler.setFormatter(JSONLogFormatter())
    logger.addHandler(handler)
    observer = Observability(logger=logger, max_series=2)
    app = Higuma(__name__, static_folder=None)

    @app.get("/items/<name>")
    async def index(name):
        return {"fast": request.state.get("_higuma_async_fast_path", False)}

    wrapped = observer.wrap_asgi(app)

    async def exercise():
        from test_gateways import scope

        incoming = asyncio.Queue()
        await incoming.put({"type": "http.request", "body": b""})
        sent = []

        async def send(event):
            sent.append(event)

        await wrapped(
            scope(
                "/items/secret-user",
                query_string=b"token=private-token",
                headers=[(b"authorization", b"Bearer PRIVATE"), (b"cookie", b"secret=COOKIE")],
            ),
            incoming.get,
            send,
        )
        assert dict(sent[0]["headers"])[b"x-request-id"]
        assert json.loads(sent[1]["body"])["fast"] is True
        await asgi_request(app, "/missing")

    asyncio.run(exercise())
    record = json.loads(output.getvalue())
    logger.removeHandler(handler)
    assert record["route"] == "/items/<name>" and record["status"] == 200
    assert all(
        value not in output.getvalue()
        for value in ("secret-user", "private-token", "PRIVATE", "COOKIE")
    )
    assert observer.snapshot()["requests"] == 1 and observer.snapshot()["in_flight"] == 0


def test_observability_metrics_cardinality_is_bounded_and_request_id_opt_in():
    observer = Observability(max_series=2, trust_request_id=True)
    app = Higuma(__name__, static_folder=None)
    app.add_middleware(observer)

    @app.get("/value/<name>")
    def index(name):
        return name, 200 + int(name)

    for index in range(8):
        response = app.test_client().get(
            "/value/" + str(index), headers={"x-request-id": "allowed-id"}
        )
        assert response.headers["x-request-id"] == "allowed-id"
    assert len(observer.snapshot()["series"]) == 2
    assert observer.snapshot()["requests"] == 8
    assert observer._identifier("invalid value with spaces") != "invalid value with spaces"


def test_health_readiness_failures_are_generic_and_timed_out_sync_checks_are_not_queued():
    health = HealthChecks(timeout=0.02)
    release, calls = threading.Event(), []

    def slow():
        calls.append(True)
        release.wait(2)
        return True

    health.add_check("database", slow)

    async def exercise():
        try:
            for _ in range(5):
                response = await health.readiness()
                assert response.status_code == 503
                assert response.get_json() == {"status": "not_ready"}
            assert calls == [True]
        finally:
            release.set()
            if health._executor:
                health._executor.shutdown(wait=True)

    asyncio.run(exercise())


def test_opentelemetry_span_export_is_explicit_and_contains_no_exception_message():
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from test_gateways import scope

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    observer = Observability(tracing=True)
    observer.tracer = provider.get_tracer("higuma-test")
    app = Higuma(__name__, static_folder=None)

    @app.get("/")
    async def index():
        return {"ok": True}

    async def exercise():
        incoming = asyncio.Queue()
        await incoming.put({"type": "http.request", "body": b""})

        async def send(event):
            pass

        await observer.wrap_asgi(app)(scope(), incoming.get, send)

    asyncio.run(exercise())
    spans = exporter.get_finished_spans()
    assert len(spans) == 1 and spans[0].attributes["http.route"] == "/"
    assert spans[0].attributes["http.response.status_code"] == 200
    provider.shutdown()


@pytest.mark.parametrize("async_handler", [False, True])
@pytest.mark.parametrize("disconnect_at", ["final", "length", "head", "no_content"])
def test_asgi_post_response_disconnect_is_not_a_failed_request(async_handler, disconnect_at):
    from test_gateways import scope

    observer = Observability()
    app = Higuma(__name__, static_folder=None)

    def index():
        if disconnect_at == "no_content":
            return "", 204
        return "bear"

    async def async_index():
        return index()

    app.get("/")(async_index if async_handler else index)

    async def exercise():
        incoming = asyncio.Queue()
        await incoming.put({"type": "http.request", "body": b""})

        async def send(event):
            complete = (
                disconnect_at in {"head", "no_content"} and event["type"] == "http.response.start"
            ) or (
                event["type"] == "http.response.body"
                and (
                    (disconnect_at == "length" and event.get("body") == b"bear")
                    or (disconnect_at == "final" and not event.get("more_body", False))
                )
            )
            if complete:
                await incoming.put({"type": "http.disconnect"})
                await asyncio.sleep(0)

        await observer.wrap_asgi(app)(
            scope(method="HEAD" if disconnect_at == "head" else "GET"), incoming.get, send
        )

    asyncio.run(exercise())
    assert observer.snapshot()["series"][0]["outcome"] == "completed"


def test_observability_middleware_records_inner_http_exception_and_cancellation():
    from higuma import RateLimitMiddleware

    app = Higuma(__name__, static_folder=None)
    observer = Observability()
    app.add_middleware(observer)
    app.add_middleware(RateLimitMiddleware(limit=1, window=60))

    @app.get("/limited")
    def limited():
        return "ok"

    client = app.test_client()
    assert client.get("/limited").status_code == 200
    assert client.get("/limited").status_code == 429

    rows = {(row["status"], row["outcome"]): row["count"] for row in observer.snapshot()["series"]}
    assert rows[(200, "completed")] == 1
    assert rows[(429, "completed")] == 1
    assert (500, "failed") not in rows

    cancel_app = Higuma(__name__, static_folder=None)
    cancel_observer = Observability()
    cancel_app.add_middleware(cancel_observer)

    @cancel_app.middleware
    def cancel_middleware(req, call_next):
        raise asyncio.CancelledError

    @cancel_app.get("/cancel")
    def cancel_view():
        return "unreachable"

    with pytest.raises(asyncio.CancelledError):
        cancel_app.test_client().get("/cancel")
    cancel_rows = {
        (row["status"], row["outcome"]): row["count"]
        for row in cancel_observer.snapshot()["series"]
    }
    assert cancel_rows[(0, "cancelled")] == 1


def test_observability_wrap_asgi_ignores_malformed_content_length():
    from higuma.observability import _parse_content_length

    assert _parse_content_length([(b"content-length", b"not-a-number")]) is None
    assert _parse_content_length([(b"content-length", b"-5")]) is None
    assert _parse_content_length([(b"content-length", b"12")]) == 12


def test_health_checks_reject_bool_nan_timeout_and_empty_name():
    for bad_timeout in (True, False, float("nan"), float("inf"), 0, 61):
        with pytest.raises(ValueError):
            HealthChecks(timeout=bad_timeout)
    health = HealthChecks()
    with pytest.raises(ValueError):
        health.add_check("", lambda: True)
