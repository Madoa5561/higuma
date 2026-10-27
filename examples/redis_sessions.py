"""Local CSRF-protected server-session demo. Never connect this demo to production Redis."""

import secrets

from higuma import (
    CSRFProtection,
    Higuma,
    RedisExtension,
    RedisRateLimitMiddleware,
    RedisSessionMiddleware,
    RedisSessionStore,
    csrf_token,
    session,
)


def create_app(redis_url: str, secret_key: bytes, *, prefix: str):
    app = Higuma(__name__, static_folder=None)
    cache = RedisExtension()
    cache.init_app(app, redis_url, async_client=False)
    store = RedisSessionStore(cache.client, prefix=prefix, ttl=300)
    app.add_middleware(RedisSessionMiddleware, secret_key, store=store)
    app.add_middleware(CSRFProtection)
    app.add_middleware(RedisRateLimitMiddleware, cache.client, prefix=prefix, limit=30, window=60)
    app.extensions["demo-session-store"] = store

    @app.get("/csrf")
    def token():
        return {"csrf_token": csrf_token()}

    @app.post("/visits")
    def visits():
        session["visits"] = session.get("visits", 0) + 1
        return {"visits": session["visits"]}

    @app.post("/logout")
    def logout():
        session.clear()
        return {"ok": True}

    return app


if __name__ == "__main__":
    import uvicorn

    app = create_app(
        "redis://127.0.0.1:6379/0",
        secrets.token_bytes(32),
        prefix="higuma-demo:" + secrets.token_hex(8) + ":",
    )
    uvicorn.run(app.asgi_app, host="127.0.0.1", port=8000, lifespan="on")
