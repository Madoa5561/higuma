# Feature guide

## WebSocket

```python
@app.websocket("/ws/<string:room>")
def chat(ws, room):
    while True:
        ws.send_json({"room": room, "message": ws.receive_json()})
```

## Multipart upload

```python
@app.post("/upload")
def upload():
    file = request.files["file"]
    file.save("uploads")
    return {"name": file.filename, "size": file.size}, 201
```

## WSGI and ASGI mounts

```python
app.mount_wsgi("/legacy", flask_app)
app.mount_asgi("/service", asgi_app)
```

The mounted application receives the path with the mount prefix removed.

## OpenAPI

```python
@app.post(
    "/users",
    summary="Create user",
    tags=("users",),
    request_body=CreateUser,
    responses={"201": {"description": "Created"}},
)
def create_user():
    return request.json, 201
```

`/openapi.json` and `/docs` are enabled by default.

## Template cache

The MiniJinja environment is shared in Rust and reuses loaded, compiled
templates. Call `app.clear_template_cache()` to force a reload in development.

## ORM

```python
class User(Model):
    id = Integer(primary_key=True, autoincrement=True)
    email = String(nullable=False, unique=True)


db = Database("sqlite:///app.db")
db.create_all(User)
with db.session() as session:
    session.add(User(email="bear@example.com"))
```

A successful session block commits; an exception rolls it back.
