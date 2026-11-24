from __future__ import annotations

import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from higuma import (
    EasyHiguma,
    EasyRecord,
    EasyTable,
    EasyUser,
    Higuma,
    UserAlreadyExistsError,
    request,
)

from examples.easy_board import create_app as create_easy_board_app
from examples.easy_custom_auth import create_app as create_easy_custom_auth_app
from examples.easy_memo import create_app as create_easy_memo_app
from examples.easy_todo import create_app as create_easy_todo_app

_CSRF_RE = re.compile(r'name="_csrf_token" value="([^"]+)"')


def _extract_csrf(html: str) -> str:
    match = _CSRF_RE.search(html)
    if match is None:
        raise AssertionError("CSRF hidden input was not found in HTML response")
    return match.group(1)


class EasyModeTests(unittest.TestCase):
    def test_higuma_easy_factory_and_table_crud(self) -> None:
        app = Higuma.easy(__name__, db=":memory:", csrf=False, hasher_n=2**12)
        self.assertIsInstance(app, EasyHiguma)

        notes = app.table("notes")
        self.assertIsInstance(notes, EasyTable)
        self.assertIs(app.table("notes"), notes)
        with self.assertRaises(ValueError):
            app.table("notes", user_scoped=True)

        first = notes.add(title="First", tag="school", done=False)
        second = notes.add({"title": "Second", "tag": "work"}, done=True)
        self.assertEqual(first["id"], 1)
        self.assertEqual(second["id"], 2)
        self.assertEqual(notes.count(), 2)
        self.assertEqual(notes.count(tag="school"), 1)

        newest = notes.list()
        self.assertEqual([item["id"] for item in newest], [2, 1])
        oldest = notes.list(newest_first=False)
        self.assertEqual([item["id"] for item in oldest], [1, 2])
        self.assertEqual(notes.list(limit=1, offset=1)[0]["id"], 1)
        self.assertEqual(notes.list(limit=0), [])

        self.assertEqual(notes.get(1)["title"], "First")  # type: ignore[index]
        self.assertEqual(notes.get("1")["title"], "First")  # type: ignore[index]
        self.assertIsNone(notes.get(999))

        updated = notes.update("1", title="First updated", done=True)
        assert updated is not None
        self.assertEqual(updated["title"], "First updated")
        self.assertTrue(updated["done"])
        self.assertEqual(updated["tag"], "school")

        self.assertTrue(notes.delete("2"))
        self.assertFalse(notes.delete(2))
        self.assertEqual(notes.count(), 1)
        self.assertEqual(notes.clear(), 1)
        self.assertEqual(notes.count(), 0)

    def test_user_registration_authentication_and_easy_user_access(self) -> None:
        app = EasyHiguma(__name__, db=":memory:", csrf=False, hasher_n=2**12)

        alice = app.register_user(" alice ", "secret123", nickname="Ali", grade=2)
        self.assertIsInstance(alice, EasyUser)
        self.assertEqual(alice.username, "alice")
        self.assertEqual(alice.nickname, "Ali")
        self.assertEqual(alice["grade"], 2)
        self.assertIn("nickname", alice)
        self.assertEqual(alice.get("missing", "fallback"), "fallback")

        with self.assertRaises(UserAlreadyExistsError):
            app.create_user("alice", "another123")
        with self.assertRaises(ValueError):
            app.signup_user("bob", "123")
        with self.assertRaises(ValueError):
            app.register_user("bob", "secret123", id=99)

        self.assertIsNone(app.authenticate_user("alice", "wrong", login=False))
        authed = app.authenticate_user("alice", "secret123", login=False)
        assert authed is not None
        self.assertEqual(authed.id, alice.id)
        self.assertEqual(app.get_user("alice"), alice)
        self.assertEqual(app.get_user_by_id(alice.id), alice)
        self.assertEqual(len(app.list_users()), 1)

    def test_user_scoped_table_isolates_records_per_user(self) -> None:
        app = EasyHiguma(__name__, db=":memory:", csrf=False, hasher_n=2**12)
        memos = app.table("memos", user_scoped=True)

        alice = app.register_user("alice", "pass1234")
        bob = app.register_user("bob", "pass1234")

        @app.post("/login")
        def do_login():
            payload = request.json
            user = app.login(payload["username"], payload["password"])
            if user is None:
                return {"error": "invalid"}, 401
            return {"user": user.to_dict()}

        @app.post("/memos")
        @app.login_required
        def create_memo():
            return memos.add(text=request.json["text"]), 201

        @app.get("/memos")
        @app.login_required
        def list_memos():
            return {"memos": memos.list(), "count": memos.count()}

        @app.get("/memos/<int:memo_id>")
        @app.login_required
        def get_memo(memo_id: int):
            return memos.get_or_404(memo_id)

        @app.patch("/memos/<int:memo_id>")
        @app.login_required
        def update_memo(memo_id: int):
            return memos.update_or_404(memo_id, text=request.json["text"])

        @app.delete("/memos/<int:memo_id>")
        @app.login_required
        def delete_memo(memo_id: int):
            memos.delete_or_404(memo_id)
            return {"deleted": memo_id}

        alice_client = app.test_client()
        bob_client = app.test_client()

        self.assertEqual(alice_client.get("/memos").status_code, 401)
        self.assertEqual(
            alice_client.post(
                "/login", json={"username": "alice", "password": "pass1234"}
            ).status_code,
            200,
        )
        self.assertEqual(
            bob_client.post("/login", json={"username": "bob", "password": "pass1234"}).status_code,
            200,
        )

        created_alice = alice_client.post("/memos", json={"text": "Alice secret memo"})
        self.assertEqual(created_alice.status_code, 201)
        alice_memo_id = created_alice.json["id"]
        self.assertEqual(created_alice.json["user_id"], alice.id)

        created_bob = bob_client.post("/memos", json={"text": "Bob memo"})
        self.assertEqual(created_bob.status_code, 201)
        bob_memo_id = created_bob.json["id"]
        self.assertEqual(created_bob.json["user_id"], bob.id)

        # Bob cannot view, update, or delete Alice's memo
        self.assertEqual(bob_client.get(f"/memos/{alice_memo_id}").status_code, 404)
        self.assertEqual(
            bob_client.patch(f"/memos/{alice_memo_id}", json={"text": "hacked"}).status_code,
            404,
        )
        self.assertEqual(bob_client.delete(f"/memos/{alice_memo_id}").status_code, 404)

        # Alice only sees her own memo
        alice_list = alice_client.get("/memos").json
        self.assertEqual(alice_list["count"], 1)
        self.assertEqual(alice_list["memos"][0]["text"], "Alice secret memo")

        # Programmatic user_id override works outside request context
        self.assertEqual(len(memos.list(user_id=bob.id)), 1)
        self.assertEqual(memos.get(bob_memo_id, user_id=bob.id)["text"], "Bob memo")  # type: ignore[index]

    def test_builtin_auth_routes_html_and_open_redirect_protection(self) -> None:
        app = create_easy_memo_app(":memory:")
        client = app.test_client()

        # Unauthenticated browser request redirects to /login?next=/
        res = client.get("/")
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.headers["location"], "/login?next=/")

        # Register page provides CSRF hidden input
        reg_page = client.get("/register?next=/")
        self.assertEqual(reg_page.status_code, 200)
        token = _extract_csrf(reg_page.text)

        # Register new user and prevent open redirect on untrusted next
        reg_post = client.post(
            "/register",
            data={
                "_csrf_token": token,
                "username": "student1",
                "password": "password123",
                "next": "//evil.example/phish",
            },
        )
        self.assertEqual(reg_post.status_code, 302)
        self.assertEqual(reg_post.headers["location"], "/")

        # Authenticated home page renders user's memo board
        home = client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("student1 さんのメモ帳", home.text)
        home_csrf = _extract_csrf(home.text)

        # Add a memo via HTML form
        add_res = client.post(
            "/memos",
            data={"_csrf_token": home_csrf, "title": "宿題", "body": "Pythonの課題を出す"},
        )
        self.assertEqual(add_res.status_code, 302)

        home_after_add = client.get("/")
        self.assertIn("宿題", home_after_add.text)
        self.assertIn("Pythonの課題を出す", home_after_add.text)

        # Delete the memo via HTML form
        delete_res = client.post(
            "/memos/1/delete",
            data={"_csrf_token": _extract_csrf(home_after_add.text)},
        )
        self.assertEqual(delete_res.status_code, 302)
        self.assertNotIn("Pythonの課題を出す", client.get("/").text)

        # Logout via GET link and verify redirect back to /login
        logout_res = client.get("/logout")
        self.assertEqual(logout_res.status_code, 302)
        self.assertEqual(logout_res.headers["location"], "/login")
        self.assertEqual(client.get("/").status_code, 302)

    def test_builtin_auth_routes_json_flow(self) -> None:
        app = EasyHiguma(__name__, db=":memory:", csrf=False, auth_routes=True, hasher_n=2**12)
        memos = app.table("memos", user_scoped=True)

        @app.get("/api/memos")
        @app.login_required
        def list_memos():
            return {"memos": memos.list()}

        client = app.test_client()

        # JSON client receives 401 instead of 302 redirect
        unauth = client.get("/api/memos", headers={"Accept": "application/json"})
        self.assertEqual(unauth.status_code, 401)

        reg = client.post(
            "/register",
            json={"username": "json_user", "password": "secret"},
        )
        self.assertEqual(reg.status_code, 201)
        self.assertEqual(reg.json["user"]["username"], "json_user")

        dup = client.post(
            "/register",
            json={"username": "json_user", "password": "secret"},
        )
        self.assertEqual(dup.status_code, 409)

        out = client.post("/logout", json={})
        self.assertEqual(out.status_code, 200)
        self.assertTrue(out.json["logged_out"])

        bad_login = client.post(
            "/login",
            json={"username": "json_user", "password": "wrong"},
        )
        self.assertEqual(bad_login.status_code, 401)

        good_login = client.post(
            "/login",
            json={"username": "json_user", "password": "secret"},
        )
        self.assertEqual(good_login.status_code, 200)
        self.assertEqual(good_login.json["user"]["username"], "json_user")

    def test_easy_validation_errors(self) -> None:
        app = EasyHiguma(__name__, db=":memory:", csrf=False, hasher_n=2**12)
        items = app.table("items")

        with self.assertRaises(ValueError):
            EasyHiguma(__name__, db="postgresql://localhost/db")
        with self.assertRaises(ValueError):
            EasyHiguma(__name__, db=":memory:", min_password_length=0)
        with self.assertRaises(ValueError):
            app.table("sqlite_master")
        with self.assertRaises(ValueError):
            app.table("_higuma_easy_users")
        with self.assertRaises(ValueError):
            items.add(id=1)
        with self.assertRaises(TypeError):
            items.get(True)
        with self.assertRaises(ValueError):
            items.get("not-a-number")
        with self.assertRaises(ValueError):
            items.list(limit=-1)
        with self.assertRaises(ValueError):
            items.list(offset=-1)

    def test_legacy_sqlite_table_without_data_json_is_automatically_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memo.db"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "CREATE TABLE memos ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "title TEXT NOT NULL, "
                    "body TEXT NOT NULL"
                    ")"
                )
                conn.execute(
                    "INSERT INTO memos (title, body) VALUES (?, ?)",
                    ("Old title", "Old body"),
                )
                conn.commit()
            finally:
                conn.close()

            # Opening the pre-existing memo.db as a global table migrates rows into data_json
            app = EasyHiguma(__name__, db=db_path, csrf=False, hasher_n=2**12)
            memos = app.table("memos", user_scoped=False)
            existing = memos.list()
            self.assertEqual(len(existing), 1)
            self.assertIsInstance(existing[0], EasyRecord)
            self.assertEqual(existing[0].title, "Old title")
            self.assertEqual(existing[0]["body"], "Old body")

            added = memos.add(title="New title", body="New body")
            self.assertEqual(added.title, "New title")
            self.assertEqual(memos.count(), 2)

            # Also verify user_scoped=True on a pre-existing legacy table with incompatible columns
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "CREATE TABLE private_memos ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "user_id INTEGER, "
                    "legacy_text TEXT NOT NULL"
                    ")"
                )
                conn.execute(
                    "INSERT INTO private_memos (user_id, legacy_text) VALUES (1, 'old row')"
                )
                conn.commit()
            finally:
                conn.close()

            private_memos = app.table("private_memos", user_scoped=True)
            user = app.register_user("student", "pass1234")
            self.assertEqual(private_memos.list(user_id=user.id), [])
            created = private_memos.add(title="Clean memo", body="Works", user_id=user.id)
            self.assertEqual(created.title, "Clean memo")
            self.assertEqual(len(private_memos.list(user_id=user.id)), 1)

    def test_easy_todo_board_and_custom_auth_examples(self) -> None:
        # 1. easy_todo.py
        todo_app = create_easy_todo_app(":memory:")
        todo_client = todo_app.test_client()
        reg_html = todo_client.get("/register").text
        todo_client.post(
            "/register",
            data={
                "_csrf_token": _extract_csrf(reg_html),
                "username": "taro",
                "password": "password123",
            },
        )
        home_html = todo_client.get("/").text
        todo_client.post(
            "/todos",
            data={
                "_csrf_token": _extract_csrf(home_html),
                "task": "レポート提出",
                "due": "明日",
            },
        )
        after_add = todo_client.get("/")
        self.assertIn("レポート提出", after_add.text)
        todo_client.post(
            "/todos/1/toggle",
            data={"_csrf_token": _extract_csrf(after_add.text)},
        )
        self.assertIn("レポート提出", todo_client.get("/?filter=done").text)
        self.assertNotIn("レポート提出", todo_client.get("/?filter=active").text)

        # 2. easy_board.py
        board_app = create_easy_board_app(":memory:")
        c1 = board_app.test_client()
        c2 = board_app.test_client()
        c1.post(
            "/register",
            data={
                "_csrf_token": _extract_csrf(c1.get("/register").text),
                "username": "alice",
                "password": "password123",
            },
        )
        c2.post(
            "/register",
            data={
                "_csrf_token": _extract_csrf(c2.get("/register").text),
                "username": "bob",
                "password": "password123",
            },
        )
        c1.post(
            "/posts",
            data={
                "_csrf_token": _extract_csrf(c1.get("/").text),
                "message": "こんにちは！",
            },
        )
        bob_view = c2.get("/")
        self.assertIn("こんにちは！", bob_view.text)
        # Bob likes Alice's post
        c2.post("/posts/1/like", data={"_csrf_token": _extract_csrf(bob_view.text)})
        self.assertIn("👍 1", c1.get("/").text)
        # Bob cannot delete Alice's post
        forbidden = c2.post(
            "/posts/1/delete",
            data={"_csrf_token": _extract_csrf(c2.get("/").text)},
        )
        self.assertEqual(forbidden.status_code, 403)

        # 3. easy_custom_auth.py
        custom_app = create_easy_custom_auth_app(":memory:")
        cc = custom_app.test_client()
        self.assertEqual(cc.get("/").headers["location"], "/login?next=/")
        signup_html = cc.get("/signup").text
        cc.post(
            "/signup",
            data={
                "_csrf_token": _extract_csrf(signup_html),
                "username": "hanako",
                "password": "password123",
                "nickname": "花子",
                "department": "情報工学科 2年",
            },
        )
        profile = cc.get("/")
        self.assertIn("ようこそ、花子 さん（学部・クラス: 情報工学科 2年）", profile.text)
        cc.post(
            "/diaries",
            data={
                "_csrf_token": _extract_csrf(profile.text),
                "entry": "Higumaでログイン機能を作った",
            },
        )
        self.assertIn("Higumaでログイン機能を作った", cc.get("/").text)


if __name__ == "__main__":
    unittest.main()
