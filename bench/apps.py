"""Equivalent benchmark workloads; no application imports optional frameworks eagerly."""

import argparse
import asyncio
import inspect
import time
from pathlib import Path

JSON_VALUE = {"message": "hello", "value": 42}
CPU_VALUE = sum(number * number for number in range(10000))
TEMPLATE = "<!doctype html><h1>Hello bear</h1><ul>{% for item in items %}<li>{{ item }}</li>{% endfor %}</ul>"


def cpu_work():
    return {"value": sum(number * number for number in range(10000))}


def create_higuma():
    from higuma import Higuma, request

    app = Higuma(
        __name__,
        static_folder=None,
        template_folder=str(Path(__file__).parent / "templates"),
        max_content_length=32 * 1024 * 1024,
    )
    app.get("/json")(lambda: JSON_VALUE)
    app.get("/cpu")(cpu_work)

    @app.get("/params/<int:number>")
    def parameters(number):
        return {"id": number, "name": request.args.get("name")}

    @app.get("/io")
    async def io_wait():
        await asyncio.sleep(0.02)
        return JSON_VALUE

    @app.get("/template")
    def template():
        return app.render_template("bench.html", items=list(range(20)))

    @app.post("/upload")
    def upload():
        return {"size": sum(len(chunk) for chunk in request.iter_body())}

    return app


def create_flask():
    from flask import Flask, render_template, request

    app = Flask(__name__, static_folder=None)
    app.json.sort_keys = False
    app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024
    app.get("/json")(lambda: JSON_VALUE)
    app.get("/cpu")(cpu_work)

    @app.get("/params/<int:number>")
    def parameters(number):
        return {"id": number, "name": request.args.get("name")}

    @app.get("/io")
    def io_wait():
        time.sleep(0.02)
        return JSON_VALUE

    # A cached template object, not repeated source compilation.
    template = app.jinja_env.from_string(TEMPLATE)

    @app.get("/template")
    def render():
        return render_template(template, items=list(range(20)))

    @app.post("/upload")
    def upload():
        total = 0
        while chunk := request.stream.read(65536):
            total += len(chunk)
        return {"size": total}

    return app


def create_starlette():
    from jinja2 import Template
    from starlette.applications import Starlette
    from starlette.responses import HTMLResponse, JSONResponse
    from starlette.routing import Route

    template = Template(TEMPLATE, autoescape=True)

    async def json_response(request):
        return JSONResponse(JSON_VALUE)

    async def cpu(request):
        return JSONResponse(cpu_work())

    async def parameters(request):
        return JSONResponse(
            {"id": request.path_params["number"], "name": request.query_params.get("name")}
        )

    async def io_wait(request):
        await asyncio.sleep(0.02)
        return JSONResponse(JSON_VALUE)

    async def render(request):
        return HTMLResponse(template.render(items=list(range(20))))

    async def upload(request):
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
        return JSONResponse({"size": total})

    limits = (
        {"max_body_size": 32 * 1024 * 1024}
        if "max_body_size" in inspect.signature(Starlette).parameters
        else {}
    )
    return Starlette(
        routes=[
            Route("/json", json_response),
            Route("/params/{number:int}", parameters),
            Route("/io", io_wait),
            Route("/cpu", cpu),
            Route("/template", render),
            Route("/upload", upload, methods=["POST"]),
        ],
        **limits,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        required=True,
        choices=["higuma-native", "higuma-asgi", "higuma-wsgi", "flask-wsgi", "starlette-asgi"],
    )
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    if args.mode == "higuma-native":
        create_higuma().run(host="127.0.0.1", port=args.port, workers=1)
    elif args.mode.endswith("asgi"):
        import uvicorn

        app = create_higuma().asgi_app if args.mode == "higuma-asgi" else create_starlette()
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=args.port,
            loop="asyncio",
            http="h11",
            ws="none",
            lifespan="on",
            proxy_headers=False,
            access_log=False,
            log_level="error",
        )
    else:
        from waitress import serve

        if args.mode == "higuma-wsgi":
            app = create_higuma()
            with app.lifespan():
                serve(
                    app.wsgi_app, host="127.0.0.1", port=args.port, threads=16, connection_limit=512
                )
        else:
            serve(
                create_flask(), host="127.0.0.1", port=args.port, threads=16, connection_limit=512
            )


if __name__ == "__main__":
    main()
