import asyncio
from contextlib import asynccontextmanager

import pytest
from higuma import (
    ASGITestClient,
    Higuma,
    SessionMiddleware,
    StreamingResponse,
    WebSocketDisconnect,
    redirect,
    request,
    session,
)


def test_async_client_http_lifespan_cookies_context_and_stream_close():
    cleaned = []

    @asynccontextmanager
    async def lifespan(app):
        yield {"loop": asyncio.get_running_loop()}
        cleaned.append("lifespan")

    app = Higuma(__name__, static_folder=None, lifespan=lifespan)
    app.add_middleware(SessionMiddleware, "async-client-test-secret-at-least-32-bytes")

    @app.post("/echo")
    async def echo():
        assert request.scope["state"]["loop"] is asyncio.get_running_loop()
        session["saved"] = True
        return {"body": request.json, "root": request.root_path}

    @app.get("/read")
    def read():
        return {"saved": session.get("saved", False)}

    @app.get("/stream")
    def stream():
        async def chunks():
            try:
                yield "first"
                await asyncio.sleep(60)
            finally:
                cleaned.append("stream")

        return StreamingResponse(chunks())

    async def exercise():
        async with ASGITestClient(app, root_path="/api") as client:
            response = await client.post("/echo", json={"bear": "日本"})
            assert response.get_json() == {"body": {"bear": "日本"}, "root": "/api"}
            assert (await client.get("/read")).get_json() == {"saved": True}
            async with client.stream("/stream") as response:
                async for chunk in response.aiter_bytes():
                    assert chunk == b"first"
                    break
            assert cleaned == ["stream"]
        assert cleaned == ["stream", "lifespan"]

    asyncio.run(exercise())


def test_async_client_websocket_echo_close_and_origin_rejection():
    app = Higuma(__name__, static_folder=None)

    @app.websocket("/ws")
    async def echo(ws):
        try:
            await ws.asend_json({"echo": await ws.areceive_json()})
            await ws.aclose(1000, "done")
        except WebSocketDisconnect:
            pass

    async def exercise():
        async with app.test_async_client() as client:
            async with client.websocket_connect("/ws") as ws:
                await ws.send_json({"bear": "日本"})
                assert await ws.receive_json() == {"echo": {"bear": "日本"}}
                with pytest.raises(WebSocketDisconnect) as caught:
                    await ws.receive()
                assert caught.value.code == 1000 and caught.value.reason == "done"
            with pytest.raises(WebSocketDisconnect) as caught:
                async with client.websocket_connect(
                    "/ws", headers={"Origin": "https://evil.example"}
                ):
                    pass
            assert caught.value.code == 1008
            assert not client._active

    asyncio.run(exercise())


def test_async_client_startup_failure_and_timeout_do_not_leak_tasks():
    app = Higuma(__name__, static_folder=None)

    @app.get("/")
    async def slow():
        await asyncio.sleep(60)
        return "late"

    async def exercise():
        async with app.test_async_client(timeout=0.05) as client:
            with pytest.raises(TimeoutError):
                await client.get("/")
            assert not client._active

    asyncio.run(exercise())

    @app.on_startup
    async def broken():
        raise ValueError("private details")

    async def start():
        with pytest.raises(RuntimeError, match="Higuma lifecycle failed"):
            async with app.test_async_client():
                pass

    asyncio.run(start())


def test_async_client_follow_redirects_base_url_override_and_case_insensitive_headers():
    from higuma import Response, redirect

    app = Higuma(__name__, static_folder=None)

    @app.post("/start")
    async def start():
        return redirect("/finish", 303)

    @app.get("/finish")
    async def finish():
        return {"scheme": request.scheme, "host": request.host, "method": request.method}

    @app.get("/empty")
    async def empty():
        return Response(b"", 204)

    @app.cli.command()
    def greet(name: str):
        return f"hi {name}"

    assert app.test_cli_runner().invoke("greet bear").output.strip() == "hi bear"

    async def exercise():
        async with app.test_async_client() as client:
            followed = await client.post(
                "/start",
                data="payload",
                follow_redirects=True,
                base_url="https://example.test:8443",
            )
            assert followed.get_json() == {
                "scheme": "https",
                "host": "example.test:8443",
                "method": "GET",
            }
            assert [item.status_code for item in followed.history] == [303]

            no_content = await client.get("/empty")
            assert no_content.status_code == 204
            assert no_content.media_type is None

            async with client.stream("/finish") as streamed:
                assert "application/json" in streamed.headers["Content-Type"]

    asyncio.run(exercise())


def test_asgi_redirect_preserves_target_query_and_client_cookie_deletion():
    from higuma import RedirectResponse, Response

    app = Higuma(__name__, static_folder=None)

    @app.get("/old")
    def old():
        return RedirectResponse("/new?to=2", status=302)

    @app.get("/new")
    def new():
        return {
            "to": request.args.get("to"),
            "from": request.args.get("from"),
            "host": request.host,
        }

    @app.get("/set-cookies")
    def set_cookies():
        response = Response("ok")
        response.append_header("Set-Cookie", "filter[page]=1; Path=/")
        response.append_header("Set-Cookie", "exp=1; Path=/")
        response.append_header("Set-Cookie", "neg=1; Path=/")
        return response

    @app.get("/del-cookies")
    def del_cookies():
        response = Response("ok")
        response.append_header("Set-Cookie", "filter[page]=; Max-Age=0; Path=/")
        response.append_header("Set-Cookie", "exp=; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=/")
        response.append_header("Set-Cookie", "neg=; Max-Age=-1; Path=/")
        return response

    sync_client = app.test_client()
    assert sync_client.get("/set-cookies").status_code == 200
    assert sync_client.cookies == {"filter[page]": "1", "exp": "1", "neg": "1"}
    assert sync_client.get("/del-cookies").status_code == 200
    assert sync_client.cookies == {}

    async def exercise():
        async with app.test_async_client() as client:
            followed = await client.get(
                "/old",
                query={"from": "1"},
                base_url="https://api.example:8443",
                follow_redirects=True,
            )
            assert followed.get_json() == {"to": "2", "from": None, "host": "api.example:8443"}

    asyncio.run(exercise())


def test_asgi_test_client_root_path_redirects_and_max_redirects_validation(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "index.html").write_text("<h1>portal docs</h1>", encoding="utf-8")

    app = Higuma(__name__, static_folder=None, docs_url=None)
    app.mount_static("/docs", docs_dir, html=True)

    @app.get("/")
    def home():
        return {"page": "home"}

    @app.get("/go-root")
    def go_root():
        return redirect("/portal", 302)

    @app.get("/sub/start")
    def sub_start():
        return redirect("finish", 302)

    @app.get("/sub/finish")
    def sub_finish():
        return {"page": "finish"}

    @app.get("/escape")
    def escape():
        return redirect("/outside", 302)

    @app.get("/encoded-escape")
    def encoded_escape():
        return redirect("/portal%2Fdocs", 302)

    async def exercise():
        async with app.test_async_client(root_path="portal/") as client:
            assert client.root_path == "/portal"

            docs = await client.get("/docs", follow_redirects=True)
            assert docs.status_code == 200
            assert "portal docs" in docs.text
            assert len(docs.history) == 1
            assert docs.history[0].status_code == 308
            assert docs.history[0].headers["location"] == "/portal/docs/"

            at_root = await client.get("/go-root", follow_redirects=True)
            assert at_root.status_code == 200
            assert at_root.get_json() == {"page": "home"}

            relative = await client.get("/sub/start", follow_redirects=True)
            assert relative.status_code == 200
            assert relative.get_json() == {"page": "finish"}

            with pytest.raises(RuntimeError, match="outside root_path"):
                await client.get("/escape", follow_redirects=True)

            with pytest.raises(RuntimeError, match="outside root_path"):
                await client.get("/encoded-escape", follow_redirects=True)

            for bad_limit in (True, 0, -1):
                with pytest.raises(ValueError, match="max_redirects"):
                    await client.get("/", follow_redirects=True, max_redirects=bad_limit)

    asyncio.run(exercise())
