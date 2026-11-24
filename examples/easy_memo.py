from __future__ import annotations

from html import escape
from pathlib import Path

from higuma import EasyHiguma, HTMLResponse, redirect, request


def create_app(db: str | Path = ":memory:") -> EasyHiguma:
    app = EasyHiguma(__name__, db=db, auth_routes=True)
    memos = app.table("memos", user_scoped=True)

    @app.get("/")
    @app.login_required
    def index() -> HTMLResponse:
        items = "".join(
            f"<li><strong>{escape(str(m.get('title', '')))}</strong>: "
            f"{escape(str(m.get('body', '')))} "
            f"<form method='post' action='/memos/{m['id']}/delete' style='display:inline'>"
            f"{app.csrf_input()}<button type='submit'>削除</button></form></li>"
            for m in memos.list()
        )
        return HTMLResponse(
            f"<h1>{escape(app.current_user.username)} さんのメモ帳</h1>"
            f"<form method='post' action='/memos'>{app.csrf_input()}"
            "<input name='title' placeholder='タイトル' required> "
            "<input name='body' placeholder='内容'> "
            "<button type='submit'>追加</button></form>"
            f"<ul>{items}</ul>"
            "<p><a href='/logout'>ログアウト</a></p>"
        )

    @app.post("/memos")
    @app.login_required
    def add_memo():
        title = request.form.get("title", "").strip()
        body = request.form.get("body", "").strip()
        if title:
            memos.add(title=title, body=body)
        return redirect("/")

    @app.post("/memos/<int:memo_id>/delete")
    @app.login_required
    def remove_memo(memo_id: int):
        memos.delete(memo_id)
        return redirect("/")

    @app.get("/api/memos")
    @app.login_required
    def api_list_memos():
        return {"memos": memos.list()}

    @app.post("/api/memos")
    @app.login_required
    def api_create_memo():
        payload = request.json
        memo = memos.add(
            title=str(payload.get("title", "")).strip(),
            body=str(payload.get("body", "")).strip(),
        )
        return memo, 201

    @app.patch("/api/memos/<int:memo_id>")
    @app.login_required
    def api_update_memo(memo_id: int):
        return memos.update_or_404(memo_id, request.json)

    @app.delete("/api/memos/<int:memo_id>")
    @app.login_required
    def api_delete_memo(memo_id: int):
        memos.delete_or_404(memo_id)
        return {"deleted": memo_id}

    return app


if __name__ == "__main__":
    default_db = Path(__file__).with_name("easy_memo.sqlite3")
    create_app(default_db).run()
