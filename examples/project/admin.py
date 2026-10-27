from pathlib import Path
from typing import Annotated, Any

from higuma import Blueprint, Depends, abort, current_user, request, roles_required, url_for
from sqlalchemy import select

from .models import Note


def create_admin_blueprint(app, database):
    admin = Blueprint(
        "admin",
        __name__,
        url_prefix="/admin",
        template_folder="templates",
        static_folder="static",
        root_path=Path(__file__).parent,
    )
    notes = Blueprint("notes", __name__, url_prefix="/notes")

    @roles_required("admin")
    def authorized():
        return None

    @admin.before_request
    def require_admin():
        if request.endpoint != "admin.static":
            return authorized()
        return None

    @admin.context_processor
    def navigation():
        return {
            "project_name": "Higuma Notes",
            "style_url": url_for("admin.static", filename="project.css"),
            "script_url": url_for("admin.static", filename="project.js"),
            "token_url": url_for("auth.token"),
            "notes_api_url": url_for("admin.notes.list_notes"),
        }

    @notes.get("/")
    def index(db: Annotated[Any, Depends(database.session)]):
        rows = [
            {"id": note.id, "title": note.title}
            for note in db.scalars(select(Note).order_by(Note.id))
        ]
        return app.render_template("admin/notes.html", notes=rows)

    @notes.get("/api")
    def list_notes(db: Annotated[Any, Depends(database.session)]):
        return [
            {"id": note.id, "title": note.title}
            for note in db.scalars(select(Note).order_by(Note.id))
        ]

    @notes.post("/api")
    def create_note():
        payload = request.get_json()
        title = payload.get("title") if isinstance(payload, dict) else None
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
            abort(400, "title must contain 1 to 200 characters")
        with database.transaction() as db:
            note = Note(title=title.strip(), author_id=int(current_user.id))
            db.add(note)
            db.flush()
            result = {"id": note.id, "title": note.title}
        return result, 201

    admin.register_blueprint(notes)
    return admin
