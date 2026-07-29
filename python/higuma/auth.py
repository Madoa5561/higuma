from __future__ import annotations

import base64
import hashlib
import json
import secrets
from collections.abc import Callable, Mapping, MutableMapping
from contextvars import ContextVar, Token
from functools import wraps
from typing import Any, Generic, TypeVar
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request as URLRequest
from urllib.request import urlopen

from .exceptions import Forbidden, Unauthorized
from .request import Request, request
from .response import ResponseValue
from .security import TokenSigner
from .sessions import SessionMiddleware

UserT = TypeVar("UserT")
_current_user: ContextVar[Any] = ContextVar("higuma_current_user", default=None)


class AnonymousUser:
    id = None
    is_authenticated = False
    is_anonymous = True

    def __bool__(self) -> bool:
        return False


class _CurrentUserProxy(Generic[UserT]):
    def _get(self) -> UserT | AnonymousUser:
        user = _current_user.get()
        return user if user is not None else AnonymousUser()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get(), name)

    def __bool__(self) -> bool:
        return bool(self._get())

    def __repr__(self) -> str:
        return repr(self._get())


current_user: _CurrentUserProxy[Any] = _CurrentUserProxy()


class AuthManager:
    def __init__(
        self,
        app: Any | None = None,
        *,
        secret_key: str | bytes | None = None,
        user_loader: Callable[[str], Any] | None = None,
        session_key: str = "_user_id",
        session_options: Mapping[str, Any] | None = None,
    ) -> None:
        self.secret_key = secret_key
        self.user_loader = user_loader
        self.session_key = session_key
        self.session_options = dict(session_options or {})
        if app is not None:
            self.init_app(app)

    def init_app(self, app: Any) -> None:
        if not self.secret_key:
            raise ValueError("AuthManager requires a secret_key")
        app.add_middleware(
            SessionMiddleware,
            self.secret_key,
            **self.session_options,
        )
        app.add_middleware(self)

    def load_user(self, func: Callable[[str], Any]) -> Callable[[str], Any]:
        self.user_loader = func
        return func

    def __call__(
        self,
        current: Request,
        call_next: Callable[[Request], ResponseValue],
    ) -> ResponseValue:
        user = None
        user_id = current.session.get(self.session_key)
        if user_id is not None and self.user_loader is not None:
            user = self.user_loader(str(user_id))
        current.user = user if user is not None else AnonymousUser()
        token: Token[Any] = _current_user.set(current.user)
        try:
            return call_next(current)
        finally:
            _current_user.reset(token)

    def login_user(self, user: Any, *, remember: bool = False) -> None:
        user_id = getattr(user, "id", None)
        if user_id is None:
            raise TypeError("authenticated user must expose an id attribute")
        request.session[self.session_key] = str(user_id)
        request.session["_remember"] = bool(remember)
        request.session["_fresh"] = True

    def confirm_login(self) -> None:
        if self.session_key not in request.session:
            raise Unauthorized(detail="authentication required")
        request.session["_fresh"] = True

    def logout_user(self) -> None:
        request.session.pop(self.session_key, None)
        request.session.pop("_remember", None)
        request.session.pop("_fresh", None)

    def login_required(self, func: Callable[..., ResponseValue]):
        return login_required(func)

    def fresh_login_required(self, func: Callable[..., ResponseValue]):
        return fresh_login_required(func)

    def roles_required(self, *roles: str, match_all: bool = True):
        return roles_required(*roles, match_all=match_all)

    def permissions_required(self, *permissions: str, match_all: bool = True):
        return permissions_required(*permissions, match_all=match_all)


def login_required(func: Callable[..., ResponseValue]):
    @wraps(func)
    def decorated(*args: Any, **kwargs: Any) -> ResponseValue:
        if not current_user or not getattr(current_user, "is_authenticated", True):
            raise Unauthorized(
                detail="authentication required",
                headers={"www-authenticate": "Session"},
            )
        return func(*args, **kwargs)

    return decorated


def fresh_login_required(func: Callable[..., ResponseValue]):
    @wraps(func)
    @login_required
    def decorated(*args: Any, **kwargs: Any) -> ResponseValue:
        if not request.session.get("_fresh"):
            raise Unauthorized(detail="fresh authentication required")
        return func(*args, **kwargs)

    return decorated


def roles_required(*roles: str, match_all: bool = True):
    return _claims_required("roles", roles, match_all=match_all)


def permissions_required(*permissions: str, match_all: bool = True):
    return _claims_required("permissions", permissions, match_all=match_all)


def _claims_required(attribute: str, required: tuple[str, ...], *, match_all: bool):
    if not required:
        raise ValueError(f"at least one required {attribute[:-1]} must be provided")

    def decorator(func: Callable[..., ResponseValue]):
        @wraps(func)
        @login_required
        def decorated(*args: Any, **kwargs: Any) -> ResponseValue:
            claims = {str(value) for value in getattr(current_user, attribute, ())}
            allowed = all(value in claims for value in required)
            if not match_all:
                allowed = any(value in claims for value in required)
            if not allowed:
                raise Forbidden(detail=f"required {attribute} are missing")
            return func(*args, **kwargs)

        return decorated

    return decorator


class OAuth2Client:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        authorize_url: str,
        token_url: str,
        userinfo_url: str,
        redirect_uri: str,
        scopes: tuple[str, ...],
        secret_key: str | bytes,
        extra_authorize_params: Mapping[str, str] | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.authorize_url = authorize_url
        self.token_url = token_url
        self.userinfo_url = userinfo_url
        self.redirect_uri = redirect_uri
        self.scopes = scopes
        self.signer = TokenSigner(secret_key, salt="higuma-oauth-state")
        self.extra_authorize_params = dict(extra_authorize_params or {})

    def authorization_url(self, **params: str) -> str:
        nonce = secrets.token_urlsafe(24)
        state = self.signer.dumps({"nonce": nonce})
        active_session = _active_session()
        verifier = None
        if active_session is not None:
            verifier = secrets.token_urlsafe(48)
            active_session[self._oauth_session_key] = {
                "nonce": nonce,
                "verifier": verifier,
            }
        query = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "state": state,
            **self.extra_authorize_params,
            **params,
        }
        if verifier is not None:
            challenge = hashlib.sha256(verifier.encode("ascii")).digest()
            query["code_challenge"] = _urlsafe_b64(challenge)
            query["code_challenge_method"] = "S256"
        return f"{self.authorize_url}?{urlencode(query)}"

    def validate_state(self, state: str, *, max_age: int = 600) -> None:
        value = self.signer.loads(state, max_age=max_age)
        if not isinstance(value, dict) or "nonce" not in value:
            raise ValueError("invalid OAuth state")
        active_session = _active_session()
        if active_session is None:
            return
        expected = active_session.pop(self._oauth_session_key, None)
        if not isinstance(expected, dict) or not secrets.compare_digest(
            str(value["nonce"]),
            str(expected.get("nonce", "")),
        ):
            raise ValueError("OAuth state does not match this session")
        active_session[self._oauth_verifier_key] = expected.get("verifier")

    def fetch_token(self, code: str) -> dict[str, Any]:
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
        }
        active_session = _active_session()
        if active_session is not None:
            verifier = active_session.pop(self._oauth_verifier_key, None)
            if verifier:
                data["code_verifier"] = str(verifier)
        return self._request_json(
            self.token_url,
            method="POST",
            data=data,
            headers={"accept": "application/json"},
        )

    def userinfo(self, access_token: str) -> dict[str, Any]:
        return self._request_json(
            self.userinfo_url,
            headers={"authorization": f"Bearer {access_token}"},
        )

    @classmethod
    def google(cls, **options: Any) -> OAuth2Client:
        return cls(
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",  # nosec B106
            userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
            scopes=("openid", "email", "profile"),
            **options,
        )

    @classmethod
    def line(cls, **options: Any) -> OAuth2Client:
        return cls(
            authorize_url="https://access.line.me/oauth2/v2.1/authorize",
            token_url="https://api.line.me/oauth2/v2.1/token",  # nosec B106
            userinfo_url="https://api.line.me/v2/profile",
            scopes=("profile", "openid", "email"),
            **options,
        )

    @classmethod
    def discord(cls, **options: Any) -> OAuth2Client:
        return cls(
            authorize_url="https://discord.com/oauth2/authorize",
            token_url="https://discord.com/api/oauth2/token",  # nosec B106
            userinfo_url="https://discord.com/api/users/@me",
            scopes=("identify", "email"),
            **options,
        )

    @staticmethod
    def _request_json(
        url: str,
        *,
        method: str = "GET",
        data: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        if urlsplit(url).scheme != "https":
            raise ValueError("OAuth endpoints must use HTTPS")
        body = urlencode(data).encode() if data is not None else None
        request_object = URLRequest(
            url,
            data=body,
            method=method,
            headers={
                "content-type": "application/x-www-form-urlencoded",
                **dict(headers or {}),
            },
        )
        try:
            with urlopen(request_object, timeout=10) as response:  # nosec B310
                payload = response.read()
        except HTTPError as exc:
            raise RuntimeError(f"OAuth provider returned HTTP {exc.code}") from exc
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise TypeError("OAuth provider returned a non-object response")
        return value

    @property
    def _oauth_session_key(self) -> str:
        return f"_oauth_state_{hashlib.sha256(self.client_id.encode()).hexdigest()[:12]}"

    @property
    def _oauth_verifier_key(self) -> str:
        return f"{self._oauth_session_key}_verifier"


def _active_session() -> MutableMapping[str, Any] | None:
    try:
        value = request.session
    except (AttributeError, RuntimeError):
        return None
    return value if isinstance(value, MutableMapping) else None


def _urlsafe_b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
