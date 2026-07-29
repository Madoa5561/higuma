from pathlib import Path

from higuma import Higuma, request

app = Higuma(__name__)
upload_directory = Path(__file__).parent / "uploads"


@app.post("/upload")
def upload():
    uploaded = request.files.get("file")
    if uploaded is None:
        return {"error": "file is required"}, 400
    destination = uploaded.save(upload_directory)
    return {
        "filename": uploaded.filename,
        "content_type": uploaded.content_type,
        "size": uploaded.size,
        "saved_to": str(destination),
        "caption": request.form.get("caption", ""),
    }, 201


if __name__ == "__main__":
    app.run()
