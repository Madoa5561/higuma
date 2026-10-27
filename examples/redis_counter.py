"""Local Redis/ASGI demo. Run a dedicated Redis first; do not point this demo at production."""

import re

from higuma import Higuma, RedisExtension, abort


def create_app(redis_url: str = "redis://127.0.0.1:6379/0", *, key_prefix: str = "higuma-example:"):
    app = Higuma(__name__, static_folder=None)
    cache = RedisExtension()
    cache.init_app(app, redis_url, async_client=True, decode_responses=True)

    @app.post("/counter/<name>")
    async def increment(name):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            abort(400, "invalid counter name")
        return {"name": name, "value": await cache.client().incr(key_prefix + name)}

    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app().asgi_app, host="127.0.0.1", port=8000, lifespan="on")
