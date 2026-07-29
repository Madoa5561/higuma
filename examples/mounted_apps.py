from higuma import Higuma

app = Higuma(__name__)


def legacy_wsgi(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain; charset=utf-8")])
    return [f"WSGI path: {environ['PATH_INFO']}".encode()]


async def modern_asgi(scope, receive, send):
    await receive()
    body = f"ASGI path: {scope['path']}".encode()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain; charset=utf-8")],
        }
    )
    await send({"type": "http.response.body", "body": body})


app.mount_wsgi("/legacy", legacy_wsgi)
app.mount_asgi("/modern", modern_asgi)


if __name__ == "__main__":
    app.run()
