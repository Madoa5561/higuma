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

app = Higuma(__name__)
hasher = PasswordHasher()


@dataclass
class User:
    id: str
    username: str
    password_hash: str
    is_authenticated: bool = True


user = User("usr_example123", "higuma", hasher.hash("change-me"))
auth = AuthManager(
    app,
    secret_key=os.environ["HIGUMA_SECRET_KEY"],
    user_loader=lambda user_id: user if user_id == user.id else None,
)
app.add_middleware(CSRFProtection)


@app.get("/csrf")
def get_csrf():
    return {"csrf_token": csrf_token()}


@app.post("/login")
def login():
    if request.json.get("username") != user.username or not hasher.verify(
        request.json.get("password", ""), user.password_hash
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


if __name__ == "__main__":
    app.run()
