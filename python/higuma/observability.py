from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from contextvars import copy_context
from threading import Lock

from .exceptions import HTTPException
from .response import JSONResponse, make_response

_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "CONNECT", "TRACE"}


class JSONLogFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {
                "level": record.levelname,
                "event": record.getMessage(),
                **getattr(record, "higuma", {}),
            },
            ensure_ascii=False,
        )


class Observability:
    """Bounded local metrics and optional explicitly configured OpenTelemetry spans."""

    def __init__(self, *, logger=None, tracing=False, trust_request_id=False, max_series=1024):
        if not isinstance(max_series, int) or max_series < 2:
            raise ValueError("max_series must be at least two")
        self.logger = logger
        self.trust_request_id, self.max_series = trust_request_id, max_series
        self._lock = Lock()
        self._in_flight = 0
        self._logging_failures = 0
        self._series = {}
        self.tracer = None
        if tracing:
            from opentelemetry import trace

            self.tracer = trace.get_tracer("higuma")

    def _identifier(self, supplied=None):
        if self.trust_request_id and supplied and _REQUEST_ID.fullmatch(supplied):
            return supplied
        return secrets.token_hex(16)

    def _span(self, method, route):
        if self.tracer is None:
            return nullcontext(None)
        from opentelemetry.trace import SpanKind

        return self.tracer.start_as_current_span(
            method + " " + route,
            kind=SpanKind.SERVER,
            attributes={"http.request.method": method, "http.route": route},
            record_exception=False,
            set_status_on_exception=False,
        )

    def _started(self):
        with self._lock:
            self._in_flight += 1

    def _finished(self, method, route, status, outcome, started, identifier, span):
        elapsed = time.perf_counter() - started
        method = method if method in _METHODS else "OTHER"
        key = (method, route, status, outcome)
        with self._lock:
            self._in_flight -= 1
            if key not in self._series and len(self._series) >= self.max_series - 1:
                key = ("OTHER", "overflow", 0, "other")
            row = self._series.setdefault(
                key, {"count": 0, "duration_seconds_sum": 0.0, "duration_seconds_max": 0.0}
            )
            row["count"] += 1
            row["duration_seconds_sum"] += elapsed
            row["duration_seconds_max"] = max(row["duration_seconds_max"], elapsed)
        record = {
            "request_id": identifier,
            "method": method,
            "route": route,
            "status": status,
            "outcome": outcome,
            "duration_seconds": elapsed,
        }
        if self.logger is not None:
            try:
                self.logger.info("higuma.request", extra={"higuma": record})
            except Exception:  # noqa: BLE001 - logging backends must not change the response
                with self._lock:
                    self._logging_failures += 1
        if span is not None:
            from opentelemetry.trace import StatusCode

            if status:
                span.set_attribute("http.response.status_code", status)
            if status >= 500 or outcome != "completed":
                span.set_status(StatusCode.ERROR)

    def snapshot(self):
        with self._lock:
            rows = [
                {"method": key[0], "route": key[1], "status": key[2], "outcome": key[3], **value}
                for key, value in self._series.items()
            ]
            return {
                "in_flight": self._in_flight,
                "logging_failures": self._logging_failures,
                "requests": sum(row["count"] for row in rows),
                "series": rows,
            }

    def __call__(self, request, call_next):
        from .app import current_app

        app = current_app._get_current()
        rule = app._endpoint_rules.get(request.endpoint)
        route = rule.rule if rule else "unmatched"
        method = request.method if request.method in _METHODS else "OTHER"
        identifier = self._identifier(request.headers.get("x-request-id"))
        request.state["request_id"] = identifier
        started, status, outcome = time.perf_counter(), 500, "failed"
        with self._span(method, route) as span:
            self._started()
            try:
                response = make_response(call_next(request))
                response.headers["x-request-id"] = identifier
                status, outcome = response.status_code, "completed"
                return response
            finally:
                self._finished(method, route, status, outcome, started, identifier, span)

    def wrap_asgi(self, app):
        """Wrap Higuma externally to preserve the fast path and observe complete streams."""
        application = app.asgi_app

        async def observed(scope, receive, send):
            if scope["type"] != "http":
                return await application(scope, receive, send)
            from .gateways import _scope_request

            route = "unmatched"
            try:
                raw = _scope_request(scope)
                rule, _, _ = app._match_route(raw["path"], raw["method"])
                if rule:
                    route = rule.rule
            except (ValueError, UnicodeError, HTTPException):
                pass
            method = scope.get("method", "OTHER")
            method = method if method in _METHODS else "OTHER"
            supplied = next(
                (
                    value.decode("latin-1")
                    for key, value in scope.get("headers", [])
                    if key.lower() == b"x-request-id"
                ),
                None,
            )
            identifier = self._identifier(supplied)
            scope = {**scope, "state": {**scope.get("state", {}), "request_id": identifier}}
            started, status, outcome = time.perf_counter(), 0, "completed"
            response_complete = False

            async def sent(event):
                nonlocal status, outcome, response_complete
                if event["type"] == "http.response.start":
                    status = event["status"]
                    event = {
                        **event,
                        "headers": [
                            (key, value)
                            for key, value in event.get("headers", [])
                            if key.lower() != b"x-request-id"
                        ]
                        + [(b"x-request-id", identifier.encode())],
                    }
                try:
                    await send(event)
                    if event["type"] == "http.response.body" and not event.get("more_body", False):
                        response_complete = True
                except OSError:
                    outcome = "disconnected"
                    raise

            async def received():
                nonlocal outcome
                event = await receive()
                if event["type"] == "http.disconnect" and not response_complete:
                    outcome = "disconnected"
                return event

            with self._span(method, route) as span:
                self._started()
                try:
                    await application(scope, received, sent)
                except asyncio.CancelledError:
                    if outcome != "disconnected":
                        outcome = "cancelled"
                    raise
                except Exception:
                    if outcome != "disconnected":
                        outcome = "failed"
                    raise
                finally:
                    self._finished(method, route, status, outcome, started, identifier, span)

        return observed


class HealthChecks:
    """Explicit liveness/readiness; no exception details or dependency URLs in responses."""

    def __init__(self, *, timeout=1.0):
        if not 0 < timeout <= 60:
            raise ValueError("health check timeout must be in (0, 60]")
        self.timeout, self.ready = timeout, True
        self._checks, self._pending = {}, {}
        self._lock = Lock()
        self._executor = None

    def add_check(self, name, callback):
        if not callable(callback) or name in self._checks:
            raise ValueError("health checks need unique names and callable probes")
        self._checks[name] = callback

    async def _check(self, name, callback):
        loop = asyncio.get_running_loop()
        with self._lock:
            pending = self._pending.get(name)
            if pending is not None and not pending.done():
                return False
            if inspect.iscoroutinefunction(callback):
                pending = loop.create_task(callback())
                pending.add_done_callback(
                    lambda task: None if task.cancelled() else task.exception()
                )
            else:
                if self._executor is None:
                    self._executor = ThreadPoolExecutor(
                        max_workers=4, thread_name_prefix="higuma-health"
                    )
                pending = self._executor.submit(copy_context().run, callback)
            self._pending[name] = pending
        try:
            awaited = (
                pending if isinstance(pending, asyncio.Future) else asyncio.wrap_future(pending)
            )
            awaited.add_done_callback(
                lambda future: None if future.cancelled() else future.exception()
            )
            result = await asyncio.wait_for(asyncio.shield(awaited), self.timeout)
            if inspect.iscoroutine(result):
                result.close()
            return result is True
        except asyncio.TimeoutError:
            if isinstance(pending, asyncio.Task):
                pending.cancel()
            return False
        except Exception:  # noqa: BLE001 - health endpoints must not expose connection/error details
            return False

    async def readiness(self):
        healthy = self.ready and all(
            await asyncio.gather(
                *[self._check(name, callback) for name, callback in self._checks.items()]
            )
        )
        return JSONResponse(
            {"status": "ready" if healthy else "not_ready"},
            200 if healthy else 503,
            {"cache-control": "no-store"},
        )

    def install(self, app, *, live_path="/health/live", ready_path="/health/ready"):
        app.add_url_rule(
            live_path,
            endpoint="health.live",
            view_func=lambda: JSONResponse(
                {"status": "alive"}, headers={"cache-control": "no-store"}
            ),
            include_in_schema=False,
        )
        app.add_url_rule(
            ready_path, endpoint="health.ready", view_func=self.readiness, include_in_schema=False
        )

        @app.on_startup
        def start():
            self.ready = True

        @app.on_shutdown
        async def close():
            self.ready = False
            loop = asyncio.get_running_loop()
            tasks = [
                pending
                for pending in self._pending.values()
                if isinstance(pending, asyncio.Task)
                and not pending.done()
                and pending.get_loop() is loop
            ]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.wait(tasks, timeout=min(1.0, self.timeout))
            if self._executor is not None:
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None
