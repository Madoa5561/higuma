# Growing a Flask-style application (0.9+)

Higuma extends component organization, commands, and testing without claiming
drop-in compatibility with Flask/Click extensions.

## Nested Blueprints

```python
from higuma import Blueprint, Higuma, request, url_for

app = Higuma(__name__)
admin = Blueprint("admin", __name__, url_prefix="/admin")
users = Blueprint("users", __name__, url_prefix="/users")

@users.get("/<int:id>")
def detail(id):
    return {"id": id, "blueprint": request.blueprint,
            "url": url_for(".detail", id=id)}

admin.register_blueprint(users)
app.register_blueprint(admin)
```

The URL is `/admin/users/1`, the endpoint is `admin.users.detail`, and the current
Blueprint is `admin.users`. Relative endpoints resolve from the innermost component.

- Before hooks: app → parent → child, in registration order.
- After/teardown: child → parent → app, reverse registration order in each scope.
- Template values: app processors → parent → child → explicit values.
- Errors: for a given HTTP status, child → parent → app, then exception types.
- Unknown-URL 404/routing 405 have no selected Blueprint and use app handlers.
- Registration detects names, route collisions, and cycles before installing the tree.

`parent.register_blueprint(child, name="members", url_prefix="/members")` overrides
registration name/prefix. Re-register a tree using a distinct app-level `name=`
and `url_prefix=`. Duplicate names are rejected. Subdomains/signals are not added.

## Component resources

`Blueprint("pages", __name__, url_prefix="/pages", template_folder="templates",
static_folder="static", root_path=...)` resolves resources from the import module
unless an explicit root is given. The static endpoint is `pages.static`, by
default `/pages/static/<path:filename>`. StaticFiles path/hidden-file protections apply.
Blueprint hooks also apply to static routes: explicitly allow assets needed by a
public login page, or put them in an unauthenticated component.

Template search order is app first, then Blueprint registration order, not
automatic per-request namespacing. Prefer `templates/admin/notes.html` to avoid
collisions. MiniJinja is not a complete Jinja2 replacement.

## Application commands

```python
@app.cli.command()
def greet(name: str, *, count: int = 1, loud: bool = False):
    return (name.upper() if loud else name) * count
```

```bash
higuma --app app:app greet bear --count 2 --loud
higuma --app app:create_app routes
higuma --app app:create_app shell
```

Existing `higuma run app:app` / `higuma routes app:app` remain. Factories take no
arguments; arbitrary Python expressions are never evaluated. Commands receive
an app context and teardown, but do not start lifespan automatically. Explicitly
use `with current_app.lifespan():` with synchronous lifecycle resources when needed.

- Required ordinary `str/int/float` parameters are positional arguments.
- Defaults and keyword-only parameters become `--parameter-name` options.
- Defaulted `bool` values become flags; specifying a True-default flag makes it False.
- Async command callbacks work, but do not share an ASGI server lifespan.
- Non-None return values are printed. Never return secrets or accept passwords as shell arguments.
- This argparse API is not a Click decorator/context/complex-type compatibility layer.

`@users.cli.command()` creates `higuma --app app:app admin users command-name`
for the nested example. `run`, `routes`, and `shell` are reserved.

## CLI and ASGI testing

```python
result = app.test_cli_runner().invoke(["greet", "bear", "--count", "2"])
assert result.exit_code == 0
assert result.output.strip() == "bearbear"

async def test_http():
    async with app.test_async_client() as client:
        assert (await client.get("/admin/users/1")).status_code == 200

async def test_socket():
    async with app.test_async_client() as client:
        async with client.websocket_connect("/ws") as socket:
            await socket.send_json({"message": "hello"})
            assert await socket.receive_json() == {"echo": {"message": "hello"}}
```

The CLI runner captures process-global stdout/stderr: do not invoke it concurrently
in one process. Inspect `exit_code`, `output`, and `exception`.

`ASGITestClient(app, base_url="https://localhost", root_path="/service", timeout=5)`
is also public. Async context manages lifespan/state. HTTP verbs and `open()` buffer
responses into Response objects. The cookie jar is intentionally simple, not a
browser implementation of domain/path/SameSite/expiry. Automatic redirect following
is not added.

Use `async with client.stream(path) as response:` and
`async for chunk in response.aiter_bytes():` for incremental responses. Early exit
sends disconnect and awaits cleanup. WebSocket `receive*` raises
`WebSocketDisconnect` on close; Origin denials can be tested before acceptance.

This is an asyncio protocol client, not OS socket/TLS/proxy/server verification.
It cannot forcibly stop unbounded synchronous work. Keep real-socket tests and
operation-specific timeouts.

The [project example](https://github.com/Madoa5561/higuma/tree/main/examples/project)
combines factories, SQLAlchemy, login/CSRF/roles, nested components, and HTML/JSON.
See [integrations](integrations.md), [uploads](uploads.md), and [performance](performance.md).
