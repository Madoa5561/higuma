from higuma import Blueprint, PasswordHasher, abort, csrf_token, current_user, request
from sqlalchemy import select

from .models import User


def create_auth_blueprint(app, database, auth):
    pages = Blueprint("auth", __name__, url_prefix="/auth")
    hasher = PasswordHasher()
    # Equal-cost verification when a username is absent, without storing a real password.
    dummy_hash = hasher.hash("not-a-user-password")

    @pages.after_request
    def prevent_caching(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @pages.get("/token")
    def token():
        return {"csrf_token": csrf_token(), "authenticated": bool(current_user)}

    @pages.post("/login")
    def login():
        payload = request.get_json()
        if not isinstance(payload, dict):
            abort(400)
        name, password = payload.get("name"), payload.get("password")
        if (
            not isinstance(name, str)
            or not 1 <= len(name) <= 64
            or not isinstance(password, str)
            or len(password.encode()) > 1024
        ):
            abort(400)
        with database.transaction() as db:
            user = db.scalar(select(User).where(User.name == name))
            valid = hasher.verify(password, user.password_hash if user else dummy_hash)
            if not valid or user is None:
                abort(401, "invalid credentials")
            auth.login_user(user)
        return {"ok": True, "csrf_token": csrf_token()}

    @pages.post("/logout")
    def logout():
        auth.logout_user()
        return {"ok": True, "csrf_token": csrf_token()}

    return pages
