import os
import secrets
from pathlib import Path

from higuma import Higuma, request, secure_filename

app = Higuma(__name__)
upload_directory = Path(
    os.environ.get("HIGUMA_UPLOAD_DIR", Path(__file__).parent / "uploads")
).resolve()
allowed_suffixes = {".jpg", ".jpeg", ".png", ".pdf", ".txt"}
max_file_size = 2 * 1024 * 1024


@app.post("/upload")
def upload():
    uploaded = request.files.get("file")
    if uploaded is None:
        return {"error": "file is required"}, 400
    if uploaded.size > max_file_size:
        return {"error": "file exceeds 2 MiB"}, 413

    original_name = secure_filename(uploaded.filename)
    suffix = Path(original_name).suffix.lower()
    if suffix not in allowed_suffixes:
        return {"error": "unsupported file extension"}, 415

    stored_name = f"{secrets.token_hex(16)}{suffix}"
    destination = (upload_directory / stored_name).resolve()
    if destination.parent != upload_directory:
        return {"error": "invalid upload destination"}, 400
    uploaded.save(destination)
    return {
        "filename": original_name,
        "stored_name": stored_name,
        "content_type": uploaded.content_type,
        "size": uploaded.size,
        "caption": request.form.get("caption", ""),
    }, 201


if __name__ == "__main__":
    app.run()
