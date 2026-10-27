import asyncio
from html import unescape

import pytest


def test_project_factory_cli_auth_csrf_roles_and_nested_api(tmp_path, monkeypatch):
    pytest.importorskip("sqlalchemy")
    from examples.project import create_app

    app = create_app(
        database_url="sqlite:///" + str(tmp_path / "project.sqlite"),
        secret_key="project-example-test-secret-at-least-32-bytes",
    )
    runner = app.test_cli_runner()
    assert runner.invoke(["init-db"]).exit_code == 0
    monkeypatch.setattr("examples.project.getpass.getpass", lambda prompt: "a-test-password-1234")
    assert runner.invoke(["create-user", "bear", "--admin"]).exit_code == 0
    assert runner.invoke(["create-user", "visitor"]).exit_code == 0
    assert runner.invoke(["create-user", "bear", "--admin"]).exit_code == 1
    with app.test_client() as client:
        assert client.get("/").status_code == 200
        assert client.get("/admin/static/project.js").status_code == 200
        assert client.get("/admin/notes/api").status_code == 401
        response = client.get("/auth/token")
        assert response.headers["cache-control"] == "no-store"
        token = response.get_json()["csrf_token"]
        payload = {"name": "bear", "password": "a-test-password-1234"}
        assert client.post("/auth/login", json=payload).status_code == 403
        response = client.post("/auth/login", json=payload, headers={"X-CSRF-Token": token})
        assert response.status_code == 200
        token = response.get_json()["csrf_token"]
        headers = {"X-CSRF-Token": token}
        response = client.post(
            "/admin/notes/api", json={"title": "<script>alert(1)</script>"}, headers=headers
        )
        assert response.status_code == 201
        assert len(client.get("/admin/notes/api").get_json()) == 1
        page = client.get("/admin/notes").text
        assert "<script>alert" not in page and "&lt;script&gt;" in page
        response = client.post("/auth/logout", headers=headers)
        token = response.get_json()["csrf_token"]
        response = client.post(
            "/auth/login",
            json={"name": "visitor", "password": "a-test-password-1234"},
            headers={"X-CSRF-Token": token},
        )
        assert response.status_code == 200
        assert client.get("/admin/notes/api").status_code == 403

    async def exercise():
        async with app.test_async_client(root_path="/project") as client:
            page = await client.get("/")
            assert 'data-login-url="/project/auth/login"' in unescape(page.text)
            response = await client.get("/auth/token")
            token = response.get_json()["csrf_token"]
            response = await client.post(
                "/auth/login", json=payload, headers={"X-CSRF-Token": token}
            )
            assert response.status_code == 200
            assert (await client.get("/admin/notes/api")).status_code == 200

    asyncio.run(exercise())
