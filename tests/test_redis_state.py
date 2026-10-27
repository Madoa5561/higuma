import json
import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from higuma import (
    Conflict,
    Higuma,
    RedisRateLimitMiddleware,
    RedisSessionMiddleware,
    RedisSessionStore,
    session,
)


@pytest.fixture
def redis_state():
    redis = pytest.importorskip("redis")
    url = os.getenv("HIGUMA_TEST_REDIS_URL")
    if not url:
        pytest.skip("set HIGUMA_TEST_REDIS_URL to a dedicated test Redis")
    client = redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
    prefix = "higuma-state-tests:" + uuid.uuid4().hex + ":"
    try:
        yield client, prefix
    finally:
        for key in client.scan_iter(match=prefix + "*"):
            client.delete(key)
        client.close()


def test_redis_sessions_are_server_side_rotate_and_cannot_be_resurrected(redis_state):
    client, prefix = redis_state
    store = RedisSessionStore(client, prefix=prefix, ttl=60)
    middleware = RedisSessionMiddleware("secret" * 8, store=store)
    app = Higuma(__name__, static_folder=None)
    app.add_middleware(middleware)

    @app.post("/login")
    def login():
        session.clear()
        session["user"] = "private-user-value"
        return {"ok": True}

    @app.get("/who")
    def who():
        return {"user": session.get("user")}

    client_http = app.test_client()
    assert client_http.post("/login").status_code == 200
    cookie = client_http.cookies["higuma_session"]
    assert "private-user-value" not in cookie
    stale = middleware._load(cookie)
    sid = cookie.split(".")[0]
    assert 0 < client.ttl(store._key(sid)) <= 60
    store.revoke(sid)
    stale["user"] = "resurrected"
    with pytest.raises(Conflict):
        middleware._save(__import__("higuma").Response(), stale)
    assert client_http.get("/who").get_json()["user"] is None
    assert middleware._load("a" * 43 + "." + "é" * 64) == {}
    client_http.post("/login")
    first = client_http.cookies["higuma_session"]
    client_http.post("/login")
    assert first != client_http.cookies["higuma_session"]
    assert store.load(first.split(".")[0]) is None


def test_redis_rate_limit_shared_by_two_app_instances_is_atomic(redis_state):
    client, prefix = redis_state
    apps = []
    for _ in range(2):
        app = Higuma(__name__, static_folder=None)
        app.add_middleware(RedisRateLimitMiddleware(client, prefix=prefix, limit=7, window=10))
        app.add_url_rule("/", endpoint="index", view_func=lambda: "bear")
        apps.append(app)

    def call(index):
        return apps[index % 2].test_client().get("/").status_code

    with ThreadPoolExecutor(max_workers=12) as pool:
        statuses = list(pool.map(call, range(40)))
    assert statuses.count(200) == 7 and statuses.count(429) == 33
    keys = list(client.scan_iter(match=prefix + "*"))
    assert len(keys) == 1 and 0 < client.pttl(keys[0]) <= 10000


def test_redis_quota_is_shared_across_real_processes(redis_state):
    _, prefix = redis_state
    script = """
import json, os, sys
from types import SimpleNamespace
from redis import Redis
from higuma import RedisRateLimitMiddleware, Response, TooManyRequests
client = Redis.from_url(os.environ['HIGUMA_TEST_REDIS_URL'], socket_timeout=2)
limiter = RedisRateLimitMiddleware(client, prefix=sys.argv[1], limit=6, window=30)
statuses = []
try:
    for _ in range(10):
        try:
            statuses.append(limiter(SimpleNamespace(client_addr='shared'), lambda _: Response()).status_code)
        except TooManyRequests:
            statuses.append(429)
    print(json.dumps(statuses))
finally:
    client.close()
"""
    children = [
        subprocess.Popen(
            [sys.executable, "-c", script, prefix],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    statuses = []
    try:
        for child in children:
            output, errors = child.communicate(timeout=15)
            assert child.returncode == 0, errors
            statuses.extend(json.loads(output))
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)
    assert statuses.count(200) == 6 and statuses.count(429) == 14


def test_redis_outage_fails_closed_without_leaking_backend_error():
    redis = pytest.importorskip("redis")

    class BrokenClient:
        def eval(self, *args):
            raise redis.ConnectionError("private-backend-credential")

    app = Higuma(__name__, static_folder=None)
    app.add_middleware(RedisRateLimitMiddleware(BrokenClient(), prefix="failure-test:"))
    reached = []

    @app.get("/")
    def index():
        reached.append(True)
        return "must not execute"

    response = app.test_client().get("/")
    assert response.status_code == 503 and not reached
    assert b"private-backend-credential" not in response.body
