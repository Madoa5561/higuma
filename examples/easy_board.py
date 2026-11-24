from __future__ import annotations

from html import escape
from pathlib import Path

from higuma import EasyHiguma, Forbidden, HTMLResponse, redirect, request


def create_app(db: str | Path = ":memory:") -> EasyHiguma:
    # user_scoped=False にすると、全ユーザーで共有される掲示板テーブルになります
    app = EasyHiguma(__name__, db=db, auth_routes=True)
    posts = app.table("board_posts", user_scoped=False)

    @app.get("/")
    @app.login_required
    def index() -> HTMLResponse:
        items = []
        for p in posts.list():
            delete_btn = (
                f"<form method='post' action='/posts/{p['id']}/delete' style='display:inline'>"
                f"{app.csrf_input()}<button type='submit'>削除</button></form>"
                if p.get("author_id") == app.current_user.id
                else ""
            )
            items.append(
                f"<li style='margin-bottom:0.75rem'>"
                f"<strong>{escape(str(p.get('author', '')))}</strong>: "
                f"{escape(str(p.get('message', '')))} "
                f"<form method='post' action='/posts/{p['id']}/like' style='display:inline'>"
                f"{app.csrf_input()}<button type='submit'>👍 {int(p.get('likes', 0))}</button></form> "
                f"{delete_btn}</li>"
            )
        return HTMLResponse(
            "<!doctype html><html lang='ja'><head><meta charset='utf-8'>"
            "<title>みんなの掲示板 - EasyHiguma</title></head>"
            "<body style='font-family:system-ui,sans-serif;max-width:680px;margin:2rem auto;padding:0 1rem'>"
            f"<h1>みんなの掲示板（ログイン中: {escape(app.current_user.username)}）</h1>"
            f"<form method='post' action='/posts' style='margin-bottom:1.5rem'>{app.csrf_input()}"
            "<input name='message' placeholder='ひとことメッセージ' required style='width:65%'> "
            "<button type='submit'>投稿する</button></form>"
            f"<ul>{''.join(items)}</ul>"
            "<p><a href='/logout'>ログアウト</a></p>"
            "</body></html>"
        )

    @app.post("/posts")
    @app.login_required
    def create_post():
        message = request.form.get("message", "").strip()
        if message:
            posts.add(
                author=app.current_user.username,
                author_id=app.current_user.id,
                message=message,
                likes=0,
            )
        return redirect("/")

    @app.post("/posts/<int:post_id>/like")
    @app.login_required
    def like_post(post_id: int):
        post = posts.get_or_404(post_id)
        posts.update(post_id, likes=int(post.get("likes", 0)) + 1)
        return redirect("/")

    @app.post("/posts/<int:post_id>/delete")
    @app.login_required
    def delete_post(post_id: int):
        post = posts.get_or_404(post_id)
        if post.get("author_id") != app.current_user.id:
            raise Forbidden(detail="自分の投稿のみ削除できます")
        posts.delete(post_id)
        return redirect("/")

    return app


if __name__ == "__main__":
    default_db = Path(__file__).with_name("easy_board.sqlite3")
    create_app(default_db).run()
