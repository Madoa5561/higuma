"""Factory-based demo. Explicit init-db/create-user CLI; no production configuration is loaded."""

import getpass
import secrets
from dataclasses import dataclass
from pathlib import Path

from higuma import AuthManager, CSRFProtection, Higuma, PasswordHasher, SQLAlchemyExtension, url_for
from sqlalchemy import select

from .admin import create_admin_blueprint
from .auth import create_auth_blueprint
from .models import Base, User


@dataclass(frozen=True)
class Identity:
    id: int
    name: str
    roles: tuple[str, ...]
    is_authenticated: bool = True


def create_app(*, database_url: str, secret_key: str | bytes) -> Higuma:
    app = Higuma(__name__, static_folder=None)
    database = SQLAlchemyExtension()
    database.init_app(app, database_url)
    auth = AuthManager(app, secret_key=secret_key)
    app.add_middleware(CSRFProtection)

    @auth.load_user
    def load_user(user_id):
        try:
            user_id = int(user_id)
        except ValueError:
            return None
        with database.transaction() as db:
            user = db.get(User, user_id)
            if user is None:
                return None
            return Identity(user.id, user.name, ("admin",) if user.admin else ())

    @app.cli.command()
    def init_db():
        """Create missing demo tables; this is not a schema migration system."""
        Base.metadata.create_all(database.engine)
        return "Demo tables initialized"

    @app.cli.command()
    def create_user(name: str, *, admin: bool = False):
        """Prompt privately for a new user's password; never accept it as a CLI argument."""
        if not 1 <= len(name) <= 64:
            raise ValueError("name must contain 1 to 64 characters")
        password = getpass.getpass("New password (at least 12 characters): ")
        if len(password) < 12:
            raise ValueError("password must contain at least 12 characters")
        password_hash = PasswordHasher().hash(password)
        with database.transaction() as db:
            if db.scalar(select(User.id).where(User.name == name)) is not None:
                raise ValueError("user already exists")
            db.add(User(name=name, password_hash=password_hash, admin=admin))
        return "User created"

    app.register_blueprint(create_auth_blueprint(app, database, auth))
    app.register_blueprint(create_admin_blueprint(app, database))

    @app.get("/")
    def home():
        return app.render_template(
            "home.html",
            style_url=url_for("admin.static", filename="project.css"),
            script_url=url_for("admin.static", filename="project.js"),
            token_url=url_for("auth.token"),
            login_url=url_for("auth.login"),
            notes_url=url_for("admin.notes.index"),
        )

    return app


def create_demo_app() -> Higuma:
    directory = Path("target/project-demo").resolve()
    directory.mkdir(parents=True, exist_ok=True)
    # Local demo only: restarting changes the signing key and invalidates sessions.
    return create_app(
        database_url="sqlite:///" + str(directory / "notes.sqlite"),
        secret_key=secrets.token_bytes(32),
    )
