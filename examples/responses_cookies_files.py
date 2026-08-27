from pathlib import Path

from higuma import (
    FileResponse,
    Higuma,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    request,
)

app = Higuma(__name__)
example_file = Path(__file__).with_name("templates") / "index.html"


@app.get("/responses/<string:kind>")
def responses(kind: str):
    variants = {
        "html": HTMLResponse("<h1>Higuma</h1>"),
        "text": PlainTextResponse("plain text"),
        "json": JSONResponse({"kind": "json"}),
        "redirect": RedirectResponse("/responses/json", status=303),
        "empty": Response(status=204),
    }
    return variants.get(kind, ({"error": "unknown response kind"}, 404))


@app.post("/preferences")
def set_preference():
    theme = request.form.get("theme", "system")
    if theme not in {"light", "dark", "system"}:
        return {"error": "invalid theme"}, 400
    response = JSONResponse({"theme": theme})
    response.set_cookie(
        "theme",
        theme,
        max_age=30 * 24 * 60 * 60,
        secure=False,  # Use True behind HTTPS in production.
        httponly=True,
        samesite="Lax",
    )
    return response


@app.delete("/preferences")
def clear_preference():
    response = JSONResponse({"theme": None})
    response.delete_cookie("theme")
    return response


@app.get("/download")
def download():
    return FileResponse(
        example_file,
        as_attachment=True,
        filename="higuma-example.html",
    )


if __name__ == "__main__":
    app.run()
