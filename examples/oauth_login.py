import os

from higuma import Higuma, OAuth2Client, SessionMiddleware, redirect, request


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"set {name} before starting this example")
    return value


def create_app() -> Higuma:
    secret_key = required_env("HIGUMA_SECRET_KEY")
    app = Higuma(__name__)
    app.add_middleware(
        SessionMiddleware,
        secret_key,
        secure=False,  # Use True behind HTTPS in production.
    )
    oauth = OAuth2Client.google(
        client_id=required_env("GOOGLE_CLIENT_ID"),
        client_secret=required_env("GOOGLE_CLIENT_SECRET"),
        redirect_uri="http://127.0.0.1:8000/auth/google/callback",
        secret_key=secret_key,
    )

    @app.get("/login/google")
    def login_google():
        return redirect(oauth.authorization_url())

    @app.get("/auth/google/callback")
    def google_callback():
        error = request.args.get("error")
        if error:
            return {"error": error}, 400
        state = request.args.get("state")
        code = request.args.get("code")
        if not state or not code:
            return {"error": "missing state or code"}, 400
        # State is session-bound and single-use; PKCE is added automatically.
        oauth.validate_state(state)
        token = oauth.fetch_token(code)
        return oauth.userinfo(token["access_token"])

    return app


if __name__ == "__main__":
    create_app().run()
