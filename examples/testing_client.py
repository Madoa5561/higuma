from higuma import Higuma, SessionMiddleware, request


def create_app() -> Higuma:
    app = Higuma(__name__)
    app.add_middleware(SessionMiddleware, "development-only-secret-key-32-bytes")

    @app.post("/items")
    def create_item():
        request.session["last_item"] = request.json["name"]
        return {"name": request.json["name"]}, 201

    @app.post("/upload")
    def upload():
        uploaded = request.files["file"]
        return {"filename": uploaded.filename, "size": uploaded.size}

    @app.get("/last-item")
    def last_item():
        return {"name": request.session.get("last_item")}

    return app


def test_json_session_and_upload() -> None:
    client = create_app().test_client()

    created = client.post("/items", json={"name": "acorn"})
    assert created.status_code == 201
    assert created.json == {"name": "acorn"}
    assert client.get("/last-item").json == {"name": "acorn"}

    uploaded = client.post(
        "/upload",
        data={"caption": "sample"},
        files={"file": ("hello.txt", b"hello", "text/plain")},
    )
    assert uploaded.json == {"filename": "hello.txt", "size": 5}


if __name__ == "__main__":
    test_json_session_and_upload()
    print("example assertions passed")
