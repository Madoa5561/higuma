from datetime import datetime
from pathlib import Path

from higuma import DateTime, Higuma, Integer, Model, String, request

app = Higuma(__name__)
database = app.init_database(f"sqlite:///{Path(__file__).with_name('blog.sqlite3')}")


class Post(Model):
    id = Integer(primary_key=True, autoincrement=True)
    title = String(length=200, nullable=False)
    body = String(nullable=False)
    created_at = DateTime(default=datetime.utcnow, nullable=False)


database.create_all(Post)


@app.get("/posts")
def list_posts():
    with database.session() as session:
        return [post.to_dict() for post in session.query(Post).order_by("id").all()]


@app.post("/posts")
def create_post():
    with database.session() as session:
        post = session.add(Post(title=request.json["title"], body=request.json["body"]))
        return post.to_dict(), 201


@app.delete("/posts/<int:post_id>")
def delete_post(post_id: int):
    with database.session() as session:
        deleted = session.query(Post).filter_by(id=post_id).delete()
        return {"deleted": deleted}


if __name__ == "__main__":
    app.run()
