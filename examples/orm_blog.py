from datetime import datetime, timezone
from pathlib import Path

from higuma import DateTime, Higuma, Integer, Model, String, request


class Post(Model):
    id = Integer(primary_key=True, autoincrement=True)
    title = String(length=200, nullable=False)
    body = String(nullable=False)
    created_at = DateTime(default=lambda: datetime.now(timezone.utc), nullable=False)


def create_app(database_url: str | None = None) -> Higuma:
    app = Higuma(__name__)
    default_path = Path(__file__).with_name("blog.sqlite3")
    database = app.init_database(database_url or f"sqlite:///{default_path}")
    database.create_all(Post)

    @app.get("/posts")
    def list_posts():
        with database.session() as session:
            return [post.to_dict() for post in session.query(Post).order_by("id").all()]

    @app.post("/posts")
    def create_post():
        payload = request.json
        if not payload.get("title") or not payload.get("body"):
            return {"error": "title and body are required"}, 400
        with database.session() as session:
            post = session.add(Post(title=payload["title"], body=payload["body"]))
            return post.to_dict(), 201

    @app.delete("/posts/<int:post_id>")
    def delete_post(post_id: int):
        with database.session() as session:
            deleted = session.query(Post).filter_by(id=post_id).delete()
            return {"deleted": deleted}

    return app


if __name__ == "__main__":
    create_app().run()
