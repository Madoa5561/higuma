import os

from higuma import Higuma, OAuth2Client, SessionMiddleware, redirect, request

app = Higuma(__name__)
app.add_middleware(
    SessionMiddleware,
    os.environ["HIGUMA_SECRET_KEY"],
    secure=False,  # Set True behind HTTPS in production.
)
oauth = OAuth2Client.google(
    client_id=os.environ["GOOGLE_CLIENT_ID"],
    client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
    redirect_uri="http://127.0.0.1:8000/auth/google/callback",
    secret_key=os.environ["HIGUMA_SECRET_KEY"],
)


@app.get("/login/google")
def login_google():
    return redirect(oauth.authorization_url())


@app.get("/auth/google/callback")
def google_callback():
    # State is session-bound and single-use; PKCE is added automatically.
    oauth.validate_state(request.args["state"])
    token = oauth.fetch_token(request.args["code"])
    return oauth.userinfo(token["access_token"])


if __name__ == "__main__":
    app.run()
