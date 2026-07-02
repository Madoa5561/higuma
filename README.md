# higuma

`higuma` is a Flask-like Python web framework with a Rust execution core.

- Fast request path built in Rust (`axum` + `tokio`)
- Python-friendly API (`@app.get`, `@app.post`, `app.run`)
- SSR support via MiniJinja templates rendered in Rust

## Install (dev)

```bash
cd higuma
python -m pip install -U pip maturin
python -m pip install -e .
```

## Quick start

```python
from higuma import Higuma

app = Higuma(__name__, template_folder="templates")

@app.get("/")
def home():
    return app.render_template("index.html", title="higuma", name="world")

@app.get("/ping")
def ping():
    return "pong"

@app.post("/echo")
def echo(request):
    return app.jsonify({"text": request["text"]})

app.run(host="127.0.0.1", port=8000, workers=0)
```

## Flask-like behavior

- String return: `return "ok"`
- Tuple return: `return "ok", 201` / `return "ok", 201, {"x-id": "1"}`
- JSON return: `return {"ok": True}` (auto JSON)
- SSR return: `return app.render_template("index.html", name="higuma")`

## Request object passed to handler

Each handler can receive one optional `request` argument:

- `request["method"]`
- `request["path"]`
- `request["query"]`
- `request["headers"]`
- `request["body"]` (bytes)
- `request["text"]` (utf-8 decoded text)

## Example app

```bash
python examples/app.py
```

Open:
- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/ping`

## Flask comparison benchmark

```bash
python -m pip install flask
python bench/compare.py
```

This prints rough local throughput for `GET /ping` and a speedup ratio.

## Notes

- This is an initial high-performance baseline focused on low overhead and simple API.
- Async Python handlers, middleware ecosystem, and production benchmark suite can be added next.
