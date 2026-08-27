import os
from dataclasses import dataclass

from higuma import AuthManager, Higuma, TokenSigner, current_user, request


@dataclass
class User:
    id: str
    roles: tuple[str, ...]
    permissions: tuple[str, ...]
    is_authenticated: bool = True
    is_active: bool = True
    is_fresh: bool = True


def create_app() -> Higuma:
    secret_key = os.environ.get("HIGUMA_SECRET_KEY")
    if not secret_key:
        raise RuntimeError("set HIGUMA_SECRET_KEY to at least 32 random bytes")

    app = Higuma(__name__)
    users = {
        "usr_example123": User(
            "usr_example123",
            roles=("admin",),
            permissions=("reports:read", "reports:write"),
        )
    }
    auth = AuthManager(app, secret_key=secret_key, user_loader=users.get)
    signer = TokenSigner(secret_key, salt="email-action")

    @app.post("/session")
    def create_session():
        user = users.get(request.json.get("user_id", ""))
        if user is None:
            return {"error": "unknown user"}, 401
        auth.login_user(user, fresh=True)
        return {"ok": True}

    @app.get("/admin")
    @auth.roles_required("admin")
    @auth.permissions_required("reports:read")
    def admin_report():
        return {"user_id": current_user.id, "report": []}

    @app.post("/email-token")
    @auth.login_required
    def issue_email_token():
        return {"token": signer.dumps({"user_id": current_user.id})}

    @app.post("/email-token/verify")
    def verify_email_token():
        try:
            payload = signer.loads(request.json.get("token", ""), max_age=900)
        except ValueError:
            return {"error": "invalid or expired token"}, 400
        return payload

    return app


if __name__ == "__main__":
    create_app().run()
