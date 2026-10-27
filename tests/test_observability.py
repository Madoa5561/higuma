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


def test_asgi_post_response_disconnect_is_not_a_failed_request():
    from test_gateways import scope

    observer = Observability()
    app = Higuma(__name__, static_folder=None)

    @app.get("/")
    def index():
        return "bear"

    async def exercise():
        complete = asyncio.Event()
        first = True

        async def receive():
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": b""}
            await complete.wait()
            return {"type": "http.disconnect"}

        async def send(event):
            if event["type"] == "http.response.body" and not event.get("more_body", False):
                complete.set()

        await observer.wrap_asgi(app)(scope(), receive, send)

    asyncio.run(exercise())
    assert observer.snapshot()["series"][0]["outcome"] == "completed"
