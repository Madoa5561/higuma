"""One application, native / ASGI / WSGI deployment. See docs/server-interfaces.md."""

import argparse
import asyncio
import hashlib
from contextlib import asynccontextmanager
from socketserver import TCPServer
from wsgiref.simple_server import WSGIServer, make_server

from higuma import EventSourceResponse, Higuma, ServerSentEvent, WebSocketDisconnect, request


class LoopbackWSGIServer(WSGIServer):
    def server_bind(self):
        # This local-only example does not need HTTPServer's potentially slow reverse DNS lookup.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]
        self.setup_environ()


@asynccontextmanager
async def lifespan(app):
    yield {"ready": True}


app = Higuma(__name__, static_folder=None, lifespan=lifespan)


@app.get("/")
def index():
    return {"framework": "higuma", "ready": app.state.get("ready", False)}


@app.post("/echo")
async def echo():
    await asyncio.sleep(0)
    return {"received": request.get_json()}


@app.get("/events")
def events():
    async def messages():
        for number in range(3):
            yield ServerSentEvent({"number": number}, event="count")
            await asyncio.sleep(0.05)

    return EventSourceResponse(messages())


@app.post("/upload")
def upload():
    digest = hashlib.sha256()
    size = 0
    if request.mimetype == "multipart/form-data":
        file = request.files["file"]
        while chunk := file.read(65536):
            digest.update(chunk)
            size += len(chunk)
    else:
        for chunk in request.iter_body():
            digest.update(chunk)
            size += len(chunk)
    return {"size": size, "sha256": digest.hexdigest(), "spooled": request.body_spooled}


@app.websocket("/ws")
async def socket(ws):
    try:
        while True:
            await ws.asend_json({"echo": await ws.areceive_json()})
    except WebSocketDisconnect:
        pass


asgi_app = app.asgi_app
wsgi_app = app.wsgi_app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("native", "wsgi"), default="native")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.mode == "wsgi":
        # Reference server for local demonstrations, not a production recommendation.
        with (
            app.lifespan(),
            make_server(
                "127.0.0.1", args.port, wsgi_app, server_class=LoopbackWSGIServer
            ) as server,
        ):
            server.serve_forever()
    else:
        app.run(port=args.port)
