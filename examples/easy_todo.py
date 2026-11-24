from __future__ import annotations

from html import escape
from pathlib import Path

from higuma import EasyHiguma, HTMLResponse, redirect, request


def create_app(db: str | Path = ":memory:") -> EasyHiguma:
    app = EasyHiguma(__name__, db=db, auth_routes=True)
    todos = app.table("todos", user_scoped=True)

    @app.get("/")
    @app.login_required
    def index() -> HTMLResponse:
        status_filter = request.args.get("filter", "all")
        if status_filter == "active":
            records = todos.list(done=False)
        elif status_filter == "done":
            records = todos.list(done=True)
        else:
            records = todos.list()

        rows = "".join(
            f"<li style='margin-bottom:0.5rem'>"
            f"<form method='post' action='/todos/{t['id']}/toggle' style='display:inline'>"
            f"{app.csrf_input()}"
            f"<button type='submit'>{'✅' if t.get('done') else '⬜'}</button></form> "
            f"<span style='{'text-decoration:line-through;color:#667085' if t.get('done') else ''}'>"
            f"{escape(str(t.get('task', '')))}</span> "
            f"<small style='color:#475467'>(期限: {escape(str(t.get('due', 'なし')))})</small> "
            f"<form method='post' action='/todos/{t['id']}/delete' style='display:inline'>"
            f"{app.csrf_input()}<button type='submit'>削除</button></form></li>"
            for t in records
        )
        active_count = todos.count(done=False)
        return HTMLResponse(
            "<!doctype html><html lang='ja'><head><meta charset='utf-8'>"
            "<title>ToDo・課題管理 - EasyHiguma</title></head>"
            "<body style='font-family:system-ui,sans-serif;max-width:680px;margin:2rem auto;padding:0 1rem'>"
            f"<h1>{escape(app.current_user.username)} さんのToDoリスト（未完了: {active_count}件）</h1>"
            f"<form method='post' action='/todos' style='margin-bottom:1rem'>{app.csrf_input()}"
            "<input name='task' placeholder='やるべき課題・タスク' required> "
            "<input name='due' placeholder='期限 (例: 金曜まで)'> "
            "<button type='submit'>追加</button></form>"
            "<p>表示: <a href='/?filter=all'>すべて</a> | "
            "<a href='/?filter=active'>未完了のみ</a> | "
            "<a href='/?filter=done'>完了済みのみ</a></p>"
            f"<ul>{rows}</ul>"
            "<p><a href='/logout'>ログアウト</a></p>"
            "</body></html>"
        )

    @app.post("/todos")
    @app.login_required
    def add_todo():
        task = request.form.get("task", "").strip()
        due = request.form.get("due", "").strip() or "なし"
        if task:
            todos.add(task=task, due=due, done=False)
        return redirect("/")

    @app.post("/todos/<int:todo_id>/toggle")
    @app.login_required
    def toggle_todo(todo_id: int):
        item = todos.get_or_404(todo_id)
        todos.update(todo_id, done=not bool(item.get("done")))
        return redirect("/")

    @app.post("/todos/<int:todo_id>/delete")
    @app.login_required
    def delete_todo(todo_id: int):
        todos.delete(todo_id)
        return redirect("/")

    return app


if __name__ == "__main__":
    default_db = Path(__file__).with_name("easy_todo.sqlite3")
    create_app(default_db).run()
