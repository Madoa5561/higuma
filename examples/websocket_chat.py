from higuma import Higuma, WebSocketDisconnect

app = Higuma(__name__)


@app.websocket("/ws/<string:room>")
def chat(websocket, room: str):
    websocket.send_json({"event": "connected", "room": room})
    try:
        while True:
            message = websocket.receive_json()
            websocket.send_json({"event": "message", "room": room, "message": message})
    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    app.run()
