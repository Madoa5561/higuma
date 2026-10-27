import asyncio
import os
import uuid

import pytest


def test_async_ecosystem_example_notes_and_readiness():
    pytest.importorskip("aiosqlite")
    from examples.async_ecosystem import create_app

    app = create_app("sqlite+aiosqlite://", initialize=True)

    async def exercise():
        async with app.test_async_client() as client:
            assert (await client.get("/health/ready")).status_code == 200
            assert (await client.get("/health/live")).get_json() == {"status": "alive"}
            response = await client.post("/notes", json={"title": "bear"})
            assert response.status_code == 201
            assert (await client.get("/notes")).get_json() == {"titles": ["bear"]}
            assert (await client.post("/notes", json={"title": ""})).status_code == 400

    asyncio.run(exercise())


def test_redis_session_example_requires_csrf_and_invalidates_logout():
    redis = pytest.importorskip("redis")
    url = os.getenv("HIGUMA_TEST_REDIS_URL")
    if not url:
        pytest.skip("set HIGUMA_TEST_REDIS_URL to a dedicated test Redis")
    from examples.redis_sessions import create_app

    prefix = "higuma-example-state-tests:" + uuid.uuid4().hex + ":"
    app = create_app(url, b"local-test-secret-with-at-least-32-bytes", prefix=prefix)
    try:
        with app.test_client() as client:
            token = client.get("/csrf").get_json()["csrf_token"]
            cookie = client.cookies["higuma_session"]
            assert client.post("/visits").status_code == 403
            headers = {"x-csrf-token": token}
            assert client.post("/visits", headers=headers).get_json() == {"visits": 1}
            assert client.post("/logout", headers=headers).status_code == 200
            assert "higuma_session" not in client.cookies
            client.cookies["higuma_session"] = cookie
            assert client.post("/visits", headers=headers).status_code == 403
    finally:
        cache = redis.Redis.from_url(url)
        for key in cache.scan_iter(match=prefix + "*"):
            cache.delete(key)
        cache.close()
