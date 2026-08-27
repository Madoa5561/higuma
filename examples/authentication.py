import os
from dataclasses import dataclass

from higuma import (
    AuthManager,
    CSRFProtection,
    Higuma,
    PasswordHasher,
    csrf_token,
    current_user,
    request,
)


@dataclass
class User:
    id: str
    username: str
    password_hash: str
    is_authenticated: bool = True


def create_app() -> Higuma:
    secret_key = os.environ.get("HIGUMA_SECRET_KEY")
    if not secret_key:
        raise RuntimeError("set HIGUMA_SECRET_KEY to at least 32 random bytes")

    app = Higuma(__name__)
    hasher = PasswordHasher()
    user = User("usr_example123", "higuma", hasher.hash("change-me"))
    auth = AuthManager(
        app,
        secret_key=secret_key,
        user_loader=lambda user_id: user if user_id == user.id else None,
    )
    app.add_middleware(CSRFProtection)

    @app.get("/csrf")
    def get_csrf():
        return {"csrf_token": csrf_token()}

    @app.post("/login")
    def login():
        payload = request.json
        if payload.get("username") != user.username or not hasher.verify(
            payload.get("password", ""), user.password_hash
        ):
            return {"error": "invalid credentials"}, 401
        auth.login_user(user)
        return {"ok": True}

    @app.get("/me")
    @auth.login_required
    def me():
        return {"id": current_user.id, "username": current_user.username}

    @app.post("/logout")
    @auth.login_required
    def logout():
        auth.logout_user()
        return {"ok": True}

    return app


if __name__ == "__main__":
    create_app().run()
