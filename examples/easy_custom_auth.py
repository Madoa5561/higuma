from __future__ import annotations

from html import escape
from pathlib import Path

from higuma import EasyHiguma, HTMLResponse, UserAlreadyExistsError, redirect, request


def create_app(db: str | Path = ":memory:") -> EasyHiguma:
    # auth_routes=False で独自のログイン・新規登録画面とプロフィール項目（nickname, department）を扱う例
    app = EasyHiguma(__name__, db=db, auth_routes=False)
    diaries = app.table("diaries", user_scoped=True)

    @app.get("/")
    @app.login_required(redirect_to="/login")
    def profile_home() -> HTMLResponse:
        user = app.current_user
        nickname = escape(str(user.get("nickname") or user.username))
        department = escape(str(user.get("department") or "未設定"))
        entries = "".join(
            f"<li>{escape(str(d.get('entry', '')))} <small>({escape(str(d['created_at']))})</small></li>"
            for d in diaries.list()
        )
        return HTMLResponse(
            "<!doctype html><html lang='ja'><head><meta charset='utf-8'>"
            "<title>マイページ - EasyHiguma</title></head>"
            "<body style='font-family:system-ui,sans-serif;max-width:680px;margin:2rem auto;padding:0 1rem'>"
            f"<h1>ようこそ、{nickname} さん（学部・クラス: {department}）</h1>"
            f"<form method='post' action='/diaries'>{app.csrf_input()}"
            "<input name='entry' placeholder='今日の学習ログ' required style='width:70%'> "
            "<button type='submit'>記録</button></form>"
            f"<ul>{entries}</ul>"
            "<p><a href='/logout'>ログアウト</a></p>"
            "</body></html>"
        )

    @app.post("/diaries")
    @app.login_required(redirect_to="/login")
    def add_diary():
        entry = request.form.get("entry", "").strip()
        if entry:
            diaries.add(entry=entry)
        return redirect("/")

    @app.get("/signup")
    def signup_page() -> HTMLResponse:
        return HTMLResponse(
            "<!doctype html><html lang='ja'><head><meta charset='utf-8'><title>新規登録</title></head>"
            "<body style='font-family:system-ui,sans-serif;max-width:420px;margin:2rem auto;padding:0 1rem'>"
            "<h1>学生アカウント登録</h1>"
            f"<form method='post' action='/signup'>{app.csrf_input()}"
            "<p><label>ユーザーID: <input name='username' required></label></p>"
            "<p><label>パスワード: <input type='password' name='password' required></label></p>"
            "<p><label>ニックネーム: <input name='nickname' required></label></p>"
            "<p><label>学部・クラス: <input name='department' placeholder='情報工学科 2年'></label></p>"
            "<button type='submit'>登録してはじめる</button></form>"
            "<p><a href='/login'>ログイン画面へ</a></p></body></html>"
        )

    @app.post("/signup")
    def signup_submit():
        try:
            app.register_user(
                request.form.get("username", ""),
                request.form.get("password", ""),
                auto_login=True,
                nickname=request.form.get("nickname", "").strip(),
                department=request.form.get("department", "").strip(),
            )
        except (UserAlreadyExistsError, ValueError) as exc:
            return HTMLResponse(
                f"<p>登録エラー: {escape(str(exc))}</p><a href='/signup'>戻る</a>", status=400
            )
        return redirect("/")

    @app.get("/login")
    def login_page() -> HTMLResponse:
        return HTMLResponse(
            "<!doctype html><html lang='ja'><head><meta charset='utf-8'><title>ログイン</title></head>"
            "<body style='font-family:system-ui,sans-serif;max-width:420px;margin:2rem auto;padding:0 1rem'>"
            "<h1>ログイン</h1>"
            f"<form method='post' action='/login'>{app.csrf_input()}"
            "<p><label>ユーザーID: <input name='username' required></label></p>"
            "<p><label>パスワード: <input type='password' name='password' required></label></p>"
            "<button type='submit'>ログイン</button></form>"
            "<p><a href='/signup'>新規登録はこちら</a></p></body></html>"
        )

    @app.post("/login")
    def login_submit():
        user = app.login(request.form.get("username", ""), request.form.get("password", ""))
        if user is None:
            return HTMLResponse(
                "<p>ユーザーIDまたはパスワードが違います</p><a href='/login'>戻る</a>", status=401
            )
        return redirect("/")

    @app.get("/logout")
    def logout_submit():
        app.logout()
        return redirect("/login")

    return app


if __name__ == "__main__":
    default_db = Path(__file__).with_name("easy_custom_auth.sqlite3")
    create_app(default_db).run()
