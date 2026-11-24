from __future__ import annotations

import inspect
import json
import os
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from .app import Higuma
from .auth import AnonymousUser, AuthManager, current_user
from .database import Database, quote_identifier
from .exceptions import BadRequest, Conflict, NotFound, Unauthorized
from .execution import resolve_awaitable
from .request import Request, _request_context, request
from .response import HTMLResponse, JSONResponse, Response, ResponseValue, redirect
from .security import CSRFProtection, PasswordHasher, _validate_secret_key, csrf_token

_AUTO = object()
_USERS_TABLE = "_higuma_easy_users"
_RESERVED_COLUMNS = {"id", "user_id", "created_at", "updated_at"}


class UserAlreadyExistsError(ValueError):
    """Raised when registering a username that already exists in EasyHiguma."""


@dataclass(frozen=True, slots=True)
class EasyUser:
    id: int
    username: str
    created_at: str
    extra: dict[str, Any] = field(default_factory=dict)
    is_authenticated: bool = True
    is_anonymous: bool = False

    def __getattr__(self, name: str) -> Any:
        extra = object.__getattribute__(self, "extra")
        if name in extra:
            return extra[name]
        raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")

    def __getitem__(self, key: str) -> Any:
        data = self.to_dict()
        if key in data:
            return data[key]
        raise KeyError(key)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in self.to_dict()

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.extra,
            "id": self.id,
            "username": self.username,
            "created_at": self.created_at,
        }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_db_url(db: str | Path) -> str:
    if isinstance(db, Path):
        return f"sqlite:///{db}"
    if not isinstance(db, str) or not db.strip():
        raise ValueError("db must be a non-empty file path or sqlite:/// URL")
    cleaned = db.strip()
    if cleaned in {":memory:", "sqlite://", "sqlite:///:memory:"}:
        return "sqlite:///:memory:"
    if cleaned.startswith("sqlite:///"):
        return cleaned
    if "://" in cleaned:
        raise ValueError("EasyHiguma built-in database supports sqlite:/// URLs or file paths")
    return f"sqlite:///{cleaned}"


def _safe_redirect_target(candidate: str | None, fallback: str) -> str:
    if not candidate or not isinstance(candidate, str):
        return fallback
    cleaned = candidate.strip()
    if (
        not cleaned.startswith("/")
        or cleaned.startswith("//")
        or "\\" in cleaned
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned)
    ):
        return fallback
    parsed = urlsplit(cleaned)
    if parsed.scheme or parsed.netloc:
        return fallback
    return cleaned


def _wants_json(current: Request) -> bool:
    if current.is_json:
        return True
    accept = str(current.headers.get("accept", "")).lower()
    return "application/json" in accept and "text/html" not in accept


class EasyTable:
    """Beginner-friendly dictionary-based SQLite table helper."""

    def __init__(
        self,
        app: EasyHiguma,
        name: str,
        *,
        user_scoped: bool = False,
    ) -> None:
        if not isinstance(name, str) or not name:
            raise ValueError("table name must be a non-empty string")
        quote_identifier(name)
        if name == _USERS_TABLE or name.startswith("sqlite_"):
            raise ValueError(f"table name {name!r} is reserved")
        self.app = app
        self.name = name
        self.user_scoped = bool(user_scoped)
        self._quoted = quote_identifier(name)
        self._ensure_table()

    def _ensure_table(self) -> None:
        index_name = quote_identifier(f"idx_{self.name}_user_id")
        with self.app.db.session() as db_session:
            db_session.execute(
                f"CREATE TABLE IF NOT EXISTS {self._quoted} ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "user_id INTEGER, "
                "data_json TEXT NOT NULL, "
                "created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL"
                ")"
            )
            db_session.execute(
                f"CREATE INDEX IF NOT EXISTS {index_name} ON {self._quoted} (user_id)"
            )

    def _resolve_user_id(self, explicit_user_id: Any) -> int | None:
        if explicit_user_id is not _AUTO:
            if explicit_user_id is None:
                if self.user_scoped:
                    raise ValueError("user_id cannot be None for a user_scoped table")
                return None
            if isinstance(explicit_user_id, bool) or not isinstance(explicit_user_id, int):
                raise TypeError("user_id must be an integer")
            return int(explicit_user_id)
        if not self.user_scoped:
            return None
        active_user = self.app.current_user
        user_id = getattr(active_user, "id", None)
        if not getattr(active_user, "is_authenticated", False) or user_id is None:
            raise Unauthorized(detail="login required to access user-scoped data")
        return int(user_id)

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        payload = json.loads(row["data_json"])
        result: dict[str, Any] = {
            **payload,
            "id": int(row["id"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }
        if self.user_scoped or row["user_id"] is not None:
            result["user_id"] = int(row["user_id"]) if row["user_id"] is not None else None
        return result

    @staticmethod
    def _clean_fields(
        data: Mapping[str, Any] | None,
        fields: Mapping[str, Any],
    ) -> dict[str, Any]:
        if data is not None and not isinstance(data, Mapping):
            raise TypeError("record data must be a mapping")
        merged = {**dict(data or {}), **dict(fields)}
        reserved = _RESERVED_COLUMNS & set(merged)
        if reserved:
            names = ", ".join(sorted(reserved))
            raise ValueError(f"reserved column names cannot be set directly: {names}")
        return merged

    def add(
        self,
        data: Mapping[str, Any] | None = None,
        *,
        user_id: Any = _AUTO,
        **fields: Any,
    ) -> dict[str, Any]:
        owner_id = self._resolve_user_id(user_id)
        payload = self._clean_fields(data, fields)
        encoded = json.dumps(payload, ensure_ascii=False)
        now = _utc_now_iso()
        with self.app.db.session() as db_session:
            cursor = db_session.execute(
                f"INSERT INTO {self._quoted} (user_id, data_json, created_at, updated_at) "  # nosec B608
                "VALUES (?, ?, ?, ?)",
                (owner_id, encoded, now, now),
            )
            record_id = int(cursor.lastrowid)
        result: dict[str, Any] = {
            **payload,
            "id": record_id,
            "created_at": now,
            "updated_at": now,
        }
        if self.user_scoped or owner_id is not None:
            result["user_id"] = owner_id
        return result

    def list(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        newest_first: bool = True,
        user_id: Any = _AUTO,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 0
        ):
            raise ValueError("limit must be a non-negative integer or None")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        if limit == 0:
            return []
        owner_id = self._resolve_user_id(user_id)
        direction = "DESC" if newest_first else "ASC"
        where = " WHERE user_id = ?" if owner_id is not None else ""
        params: tuple[Any, ...] = (owner_id,) if owner_id is not None else ()
        with self.app.db.session() as db_session:
            rows = db_session.execute(
                f"SELECT id, user_id, data_json, created_at, updated_at "  # nosec B608
                f"FROM {self._quoted}{where} ORDER BY id {direction}",
                params,
            ).fetchall()
        items = [self._row_to_dict(row) for row in rows]
        if filters:
            items = [
                item
                for item in items
                if all(item.get(key) == expected for key, expected in filters.items())
            ]
        if offset:
            items = items[offset:]
        if limit is not None:
            items = items[:limit]
        return items

    def count(self, *, user_id: Any = _AUTO, **filters: Any) -> int:
        return len(self.list(user_id=user_id, **filters))

    @staticmethod
    def _coerce_record_id(record_id: int | str) -> int:
        if isinstance(record_id, bool):
            raise TypeError("record_id must be an integer")
        if isinstance(record_id, int):
            return int(record_id)
        if isinstance(record_id, str):
            cleaned = record_id.strip()
            if cleaned and cleaned.lstrip("+-").isdigit():
                try:
                    return int(cleaned)
                except ValueError as exc:
                    raise ValueError("record_id must be an integer") from exc
            raise ValueError("record_id must be an integer")
        raise TypeError("record_id must be an integer")

    def get(self, record_id: int | str, *, user_id: Any = _AUTO) -> dict[str, Any] | None:
        parsed_id = self._coerce_record_id(record_id)
        owner_id = self._resolve_user_id(user_id)
        where = "WHERE id = ? AND user_id = ?" if owner_id is not None else "WHERE id = ?"
        params: tuple[Any, ...] = (parsed_id, owner_id) if owner_id is not None else (parsed_id,)
        with self.app.db.session() as db_session:
            row = db_session.execute(
                f"SELECT id, user_id, data_json, created_at, updated_at "  # nosec B608
                f"FROM {self._quoted} {where}",
                params,
            ).fetchone()
        return self._row_to_dict(row) if row is not None else None

    def get_or_404(self, record_id: int | str, *, user_id: Any = _AUTO) -> dict[str, Any]:
        item = self.get(record_id, user_id=user_id)
        if item is None:
            raise NotFound(detail=f"{self.name} record {record_id} was not found")
        return item

    def update(
        self,
        record_id: int | str,
        data: Mapping[str, Any] | None = None,
        *,
        user_id: Any = _AUTO,
        **changes: Any,
    ) -> dict[str, Any] | None:
        parsed_id = self._coerce_record_id(record_id)
        owner_id = self._resolve_user_id(user_id)
        updates = self._clean_fields(data, changes)
        where = "WHERE id = ? AND user_id = ?" if owner_id is not None else "WHERE id = ?"
        params: tuple[Any, ...] = (parsed_id, owner_id) if owner_id is not None else (parsed_id,)
        now = _utc_now_iso()
        with self.app.db.session() as db_session:
            row = db_session.execute(
                f"SELECT id, user_id, data_json, created_at, updated_at "  # nosec B608
                f"FROM {self._quoted} {where}",
                params,
            ).fetchone()
            if row is None:
                return None
            payload = {**json.loads(row["data_json"]), **updates}
            encoded = json.dumps(payload, ensure_ascii=False)
            db_session.execute(
                f"UPDATE {self._quoted} SET data_json = ?, updated_at = ? {where}",  # nosec B608
                (encoded, now, *params),
            )
            created_at = str(row["created_at"])
            row_user_id = int(row["user_id"]) if row["user_id"] is not None else None
        result: dict[str, Any] = {
            **payload,
            "id": parsed_id,
            "created_at": created_at,
            "updated_at": now,
        }
        if self.user_scoped or row_user_id is not None:
            result["user_id"] = row_user_id
        return result

    def update_or_404(
        self,
        record_id: int | str,
        data: Mapping[str, Any] | None = None,
        *,
        user_id: Any = _AUTO,
        **changes: Any,
    ) -> dict[str, Any]:
        updated = self.update(record_id, data, user_id=user_id, **changes)
        if updated is None:
            raise NotFound(detail=f"{self.name} record {record_id} was not found")
        return updated

    def delete(self, record_id: int | str, *, user_id: Any = _AUTO) -> bool:
        parsed_id = self._coerce_record_id(record_id)
        owner_id = self._resolve_user_id(user_id)
        where = "WHERE id = ? AND user_id = ?" if owner_id is not None else "WHERE id = ?"
        params: tuple[Any, ...] = (parsed_id, owner_id) if owner_id is not None else (parsed_id,)
        with self.app.db.session() as db_session:
            cursor = db_session.execute(
                f"DELETE FROM {self._quoted} {where}",  # nosec B608
                params,
            )
            return cursor.rowcount > 0

    def delete_or_404(self, record_id: int | str, *, user_id: Any = _AUTO) -> None:
        if not self.delete(record_id, user_id=user_id):
            raise NotFound(detail=f"{self.name} record {record_id} was not found")

    def clear(self, *, user_id: Any = _AUTO) -> int:
        owner_id = self._resolve_user_id(user_id)
        where = " WHERE user_id = ?" if owner_id is not None else ""
        params: tuple[Any, ...] = (owner_id,) if owner_id is not None else ()
        with self.app.db.session() as db_session:
            cursor = db_session.execute(
                f"DELETE FROM {self._quoted}{where}",  # nosec B608
                params,
            )
            return int(cursor.rowcount)


class EasyHiguma(Higuma):
    """Student- and prototype-friendly Higuma mode with built-in SQLite, users, sessions, and CRUD."""

    def __init__(
        self,
        import_name: str = "__main__",
        *,
        db: str | Path = "sqlite:///higuma.db",
        secret_key: str | bytes | None = None,
        csrf: bool = True,
        auth_routes: bool = False,
        login_url: str = "/login",
        register_url: str = "/register",
        logout_url: str = "/logout",
        redirect_after_login: str = "/",
        redirect_after_logout: str = "/login",
        min_password_length: int = 4,
        hasher_n: int = 2**14,
        session_options: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(import_name, **kwargs)
        if (
            isinstance(min_password_length, bool)
            or not isinstance(min_password_length, int)
            or min_password_length < 1
        ):
            raise ValueError("min_password_length must be a positive integer")
        resolved_secret: str | bytes
        if secret_key is not None:
            resolved_secret = _validate_secret_key(secret_key)
        else:
            env_secret = os.getenv("HIGUMA_SECRET_KEY")
            if env_secret and len(env_secret.encode("utf-8")) >= 32:
                resolved_secret = env_secret
            else:
                resolved_secret = secrets.token_urlsafe(48)

        self.db: Database = self.init_database(_normalize_db_url(db))
        self.hasher = PasswordHasher(n=hasher_n)
        self.min_password_length = min_password_length
        self.csrf_enabled = bool(csrf)
        self.login_url = login_url
        self.register_url = register_url
        self.logout_url = logout_url
        self.redirect_after_login = redirect_after_login
        self.redirect_after_logout = redirect_after_logout
        self._tables: dict[str, EasyTable] = {}
        self._auth_routes_mounted = False

        self._ensure_users_table()
        self.auth = AuthManager(
            self,
            secret_key=resolved_secret,
            user_loader=self.get_user_by_id,
            session_options=session_options,
        )
        if self.csrf_enabled:
            self.add_middleware(CSRFProtection)
        if auth_routes:
            self.mount_auth_routes(
                login_url=login_url,
                register_url=register_url,
                logout_url=logout_url,
                redirect_after_login=redirect_after_login,
                redirect_after_logout=redirect_after_logout,
            )

    def _ensure_users_table(self) -> None:
        quoted = quote_identifier(_USERS_TABLE)
        with self.db.session() as db_session:
            db_session.execute(
                f"CREATE TABLE IF NOT EXISTS {quoted} ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "username TEXT NOT NULL UNIQUE, "
                "password_hash TEXT NOT NULL, "
                "extra_json TEXT NOT NULL, "
                "created_at TEXT NOT NULL"
                ")"
            )

    @property
    def current_user(self) -> EasyUser | AnonymousUser:
        return current_user  # type: ignore[return-value]

    def csrf_token(self) -> str:
        if not self.csrf_enabled:
            return ""
        return csrf_token()

    def csrf_input(self) -> str:
        if not self.csrf_enabled:
            return ""
        token = escape(self.csrf_token(), quote=True)
        return f'<input type="hidden" name="_csrf_token" value="{token}">'

    def table(self, name: str, *, user_scoped: bool = False) -> EasyTable:
        existing = self._tables.get(name)
        if existing is not None:
            if existing.user_scoped != bool(user_scoped):
                raise ValueError(
                    f"table {name!r} is already registered with user_scoped={existing.user_scoped}"
                )
            return existing
        table = EasyTable(self, name, user_scoped=user_scoped)
        self._tables[name] = table
        return table

    def _row_to_user(self, row: Any) -> EasyUser:
        return EasyUser(
            id=int(row["id"]),
            username=str(row["username"]),
            created_at=str(row["created_at"]),
            extra=json.loads(row["extra_json"]),
        )

    def _validate_credentials(self, username: str, password: str) -> tuple[str, str]:
        if not isinstance(username, str):
            raise TypeError("username must be a string")
        if not isinstance(password, str):
            raise TypeError("password must be a string")
        cleaned_username = username.strip()
        if (
            not cleaned_username
            or len(cleaned_username) > 150
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned_username)
        ):
            raise ValueError("username must be 1 to 150 visible characters")
        if len(password) < self.min_password_length:
            raise ValueError(
                f"password must contain at least {self.min_password_length} characters"
            )
        return cleaned_username, password

    def register_user(
        self,
        username: str,
        password: str,
        *,
        auto_login: bool = False,
        remember: bool = False,
        **extra: Any,
    ) -> EasyUser:
        cleaned_username, valid_password = self._validate_credentials(username, password)
        reserved = {"id", "username", "password", "password_hash", "created_at"} & set(extra)
        if reserved:
            raise ValueError(
                f"reserved user fields cannot be in extra: {', '.join(sorted(reserved))}"
            )
        password_hash = self.hasher.hash(valid_password)
        extra_json = json.dumps(extra, ensure_ascii=False)
        now = _utc_now_iso()
        quoted = quote_identifier(_USERS_TABLE)
        import sqlite3

        try:
            with self.db.session() as db_session:
                cursor = db_session.execute(
                    f"INSERT INTO {quoted} (username, password_hash, extra_json, created_at) "  # nosec B608
                    "VALUES (?, ?, ?, ?)",
                    (cleaned_username, password_hash, extra_json, now),
                )
                user_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise UserAlreadyExistsError(f"username {cleaned_username!r} is already taken") from exc
        user = EasyUser(
            id=user_id,
            username=cleaned_username,
            created_at=now,
            extra=dict(extra),
        )
        if auto_login and _request_context.get() is not None:
            self.auth.login_user(user, remember=remember)
        return user

    create_user = register_user
    signup_user = register_user

    def authenticate_user(
        self,
        username: str,
        password: str,
        *,
        login: bool = True,
        remember: bool = False,
    ) -> EasyUser | None:
        if not isinstance(username, str) or not isinstance(password, str):
            return None
        cleaned_username = username.strip()
        if not cleaned_username or not password:
            return None
        quoted = quote_identifier(_USERS_TABLE)
        with self.db.session() as db_session:
            row = db_session.execute(
                f"SELECT id, username, password_hash, extra_json, created_at "  # nosec B608
                f"FROM {quoted} WHERE username = ?",
                (cleaned_username,),
            ).fetchone()
        if row is None or not self.hasher.verify(password, str(row["password_hash"])):
            return None
        user = self._row_to_user(row)
        if login and _request_context.get() is not None:
            self.auth.login_user(user, remember=remember)
        return user

    def login(self, username: str, password: str, *, remember: bool = False) -> EasyUser | None:
        return self.authenticate_user(username, password, login=True, remember=remember)

    def login_user(self, user: EasyUser, *, remember: bool = False) -> EasyUser:
        self.auth.login_user(user, remember=remember)
        return user

    def logout(self) -> None:
        self.auth.logout_user()

    logout_user = logout

    def get_user(self, username: str) -> EasyUser | None:
        if not isinstance(username, str) or not username.strip():
            return None
        quoted = quote_identifier(_USERS_TABLE)
        with self.db.session() as db_session:
            row = db_session.execute(
                f"SELECT id, username, extra_json, created_at FROM {quoted} WHERE username = ?",  # nosec B608
                (username.strip(),),
            ).fetchone()
        return self._row_to_user(row) if row is not None else None

    def get_user_by_id(self, user_id: int | str) -> EasyUser | None:
        if isinstance(user_id, bool):
            return None
        try:
            parsed_id = int(user_id)
        except (TypeError, ValueError):
            return None
        quoted = quote_identifier(_USERS_TABLE)
        with self.db.session() as db_session:
            row = db_session.execute(
                f"SELECT id, username, extra_json, created_at FROM {quoted} WHERE id = ?",  # nosec B608
                (parsed_id,),
            ).fetchone()
        return self._row_to_user(row) if row is not None else None

    def list_users(self) -> list[EasyUser]:
        quoted = quote_identifier(_USERS_TABLE)
        with self.db.session() as db_session:
            rows = db_session.execute(
                f"SELECT id, username, extra_json, created_at FROM {quoted} ORDER BY id ASC"  # nosec B608
            ).fetchall()
        return [self._row_to_user(row) for row in rows]

    def login_required(
        self,
        func: Callable[..., ResponseValue] | None = None,
        *,
        redirect_to: str | None | object = _AUTO,
    ) -> Any:
        def decorate(target: Callable[..., ResponseValue]) -> Callable[..., ResponseValue]:
            if not callable(target):
                raise TypeError("login_required target must be callable")

            def check_or_redirect() -> Response | None:
                active = self.current_user
                if active and getattr(active, "is_authenticated", False):
                    return None
                effective_redirect = (
                    (self.login_url if self._auth_routes_mounted else None)
                    if redirect_to is _AUTO
                    else redirect_to
                )
                current = _request_context.get()
                if (
                    effective_redirect
                    and isinstance(effective_redirect, str)
                    and current is not None
                    and not _wants_json(current)
                ):
                    current_target = (
                        f"{current.path}?{current.query_string}"
                        if current.query_string
                        else current.path
                    )
                    sep = "&" if "?" in effective_redirect else "?"
                    return redirect(
                        f"{effective_redirect}{sep}next={quote(current_target, safe='/')}"
                    )
                raise Unauthorized(
                    detail="authentication required",
                    headers={"www-authenticate": "Session"},
                )

            if inspect.iscoroutinefunction(target):

                @wraps(target)
                async def async_wrapped(*args: Any, **kwargs: Any) -> Any:
                    early = check_or_redirect()
                    if early is not None:
                        return early
                    return await target(*args, **kwargs)

                return async_wrapped

            @wraps(target)
            def sync_wrapped(*args: Any, **kwargs: Any) -> Any:
                early = check_or_redirect()
                if early is not None:
                    return early
                return resolve_awaitable(target(*args, **kwargs))

            return sync_wrapped

        if func is not None:
            return decorate(func)
        return decorate

    def _render_auth_page(
        self,
        *,
        title: str,
        heading: str,
        action: str,
        submit_label: str,
        alt_text: str,
        alt_href: str,
        alt_label: str,
        next_target: str = "",
        error: str | None = None,
        username: str = "",
        status: int = 200,
    ) -> HTMLResponse:
        error_html = (
            f'<p role="alert" style="color:#b42318;background:#fef3f2;padding:0.6rem 0.8rem;'
            f'border-radius:6px;border:1px solid #fecdca">{escape(error)}</p>'
            if error
            else ""
        )
        next_input = (
            f'<input type="hidden" name="next" value="{escape(next_target, quote=True)}">'
            if next_target
            else ""
        )
        alt_link = (
            f"{alt_href}{'&' if '?' in alt_href else '?'}next={quote(next_target, safe='/')}"
            if next_target
            else alt_href
        )
        html = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
</head>
<body style="font-family:system-ui,sans-serif;max-width:420px;margin:3rem auto;padding:0 1rem;line-height:1.5">
  <main style="border:1px solid #d0d5dd;border-radius:10px;padding:1.5rem">
    <h1 style="margin-top:0;font-size:1.4rem">{escape(heading)}</h1>
    {error_html}
    <form method="post" action="{escape(action, quote=True)}">
      {self.csrf_input()}
      {next_input}
      <div style="margin-bottom:0.9rem">
        <label style="display:block;margin-bottom:0.25rem">ユーザー名 (Username)
          <input name="username" value="{escape(username, quote=True)}" required autocomplete="username" style="width:100%;box-sizing:border-box;padding:0.5rem">
        </label>
      </div>
      <div style="margin-bottom:1rem">
        <label style="display:block;margin-bottom:0.25rem">パスワード (Password)
          <input type="password" name="password" required style="width:100%;box-sizing:border-box;padding:0.5rem">
        </label>
      </div>
      <button type="submit" style="width:100%;padding:0.6rem;cursor:pointer">{escape(submit_label)}</button>
    </form>
    <p style="margin-bottom:0;margin-top:1rem;font-size:0.9rem">{escape(alt_text)} <a href="{escape(alt_link, quote=True)}">{escape(alt_label)}</a></p>
  </main>
</body>
</html>"""
        return HTMLResponse(html, status=status)

    def mount_auth_routes(
        self,
        *,
        login_url: str | None = None,
        register_url: str | None = None,
        logout_url: str | None = None,
        redirect_after_login: str | None = None,
        redirect_after_logout: str | None = None,
    ) -> None:
        if self._auth_routes_mounted:
            return
        self.login_url = login_url or self.login_url
        self.register_url = register_url or self.register_url
        self.logout_url = logout_url or self.logout_url
        self.redirect_after_login = redirect_after_login or self.redirect_after_login
        self.redirect_after_logout = redirect_after_logout or self.redirect_after_logout
        self._auth_routes_mounted = True

        def _extract_credentials(req: Request) -> tuple[str, str, str, bool]:
            if req.is_json:
                payload = req.get_json(silent=True)
                if not isinstance(payload, Mapping):
                    raise BadRequest(detail="expected a JSON object")
                return (
                    str(payload.get("username", "")),
                    str(payload.get("password", "")),
                    str(payload.get("next", req.args.get("next", ""))),
                    bool(payload.get("remember", False)),
                )
            return (
                str(req.form.get("username", "")),
                str(req.form.get("password", "")),
                str(req.form.get("next") or req.args.get("next", "")),
                str(req.form.get("remember", "")).lower() in {"1", "true", "on", "yes"},
            )

        def login_view() -> ResponseValue:
            if request.method == "GET":
                next_param = _safe_redirect_target(request.args.get("next"), "")
                return self._render_auth_page(
                    title="ログイン",
                    heading="ログイン",
                    action=self.login_url,
                    submit_label="ログイン",
                    alt_text="アカウントをお持ちでないですか？",
                    alt_href=self.register_url,
                    alt_label="新規登録",
                    next_target=next_param,
                )
            username, password, raw_next, remember = _extract_credentials(request)
            user = self.authenticate_user(username, password, login=True, remember=remember)
            if user is None:
                if _wants_json(request):
                    return JSONResponse({"detail": "invalid username or password"}, status=401)
                return self._render_auth_page(
                    title="ログイン",
                    heading="ログイン",
                    action=self.login_url,
                    submit_label="ログイン",
                    alt_text="アカウントをお持ちでないですか？",
                    alt_href=self.register_url,
                    alt_label="新規登録",
                    next_target=_safe_redirect_target(raw_next, ""),
                    error="ユーザー名またはパスワードが正しくありません。",
                    username=username,
                    status=401,
                )
            if _wants_json(request):
                return JSONResponse({"user": user.to_dict()})
            target = _safe_redirect_target(raw_next, self.redirect_after_login)
            return redirect(target)

        def register_view() -> ResponseValue:
            if request.method == "GET":
                next_param = _safe_redirect_target(request.args.get("next"), "")
                return self._render_auth_page(
                    title="新規登録",
                    heading="新規アカウント作成",
                    action=self.register_url,
                    submit_label="登録する",
                    alt_text="すでにアカウントをお持ちですか？",
                    alt_href=self.login_url,
                    alt_label="ログイン",
                    next_target=next_param,
                )
            username, password, raw_next, remember = _extract_credentials(request)
            try:
                user = self.register_user(
                    username,
                    password,
                    auto_login=True,
                    remember=remember,
                )
            except UserAlreadyExistsError as exc:
                if _wants_json(request):
                    raise Conflict(detail=str(exc)) from exc
                return self._render_auth_page(
                    title="新規登録",
                    heading="新規アカウント作成",
                    action=self.register_url,
                    submit_label="登録する",
                    alt_text="すでにアカウントをお持ちですか？",
                    alt_href=self.login_url,
                    alt_label="ログイン",
                    next_target=_safe_redirect_target(raw_next, ""),
                    error="そのユーザー名はすでに使われています。",
                    username=username,
                    status=409,
                )
            except ValueError as exc:
                if _wants_json(request):
                    raise BadRequest(detail=str(exc)) from exc
                return self._render_auth_page(
                    title="新規登録",
                    heading="新規アカウント作成",
                    action=self.register_url,
                    submit_label="登録する",
                    alt_text="すでにアカウントをお持ちですか？",
                    alt_href=self.login_url,
                    alt_label="ログイン",
                    next_target=_safe_redirect_target(raw_next, ""),
                    error=str(exc),
                    username=username,
                    status=400,
                )
            if _wants_json(request):
                return JSONResponse({"user": user.to_dict()}, status=201)
            target = _safe_redirect_target(raw_next, self.redirect_after_login)
            return redirect(target)

        def logout_view() -> ResponseValue:
            self.logout()
            if _wants_json(request):
                return JSONResponse({"logged_out": True})
            return redirect(self.redirect_after_logout)

        self.add_url_rule(
            self.login_url,
            endpoint="easy_auth.login",
            view_func=login_view,
            methods=("GET", "POST"),
            include_in_schema=False,
        )
        self.add_url_rule(
            self.register_url,
            endpoint="easy_auth.register",
            view_func=register_view,
            methods=("GET", "POST"),
            include_in_schema=False,
        )
        self.add_url_rule(
            self.logout_url,
            endpoint="easy_auth.logout",
            view_func=logout_view,
            methods=("GET", "POST"),
            include_in_schema=False,
        )
