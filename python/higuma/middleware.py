from __future__ import annotations

from collections.abc import Callable, Iterable

from .exceptions import Forbidden
from .request import Request
from .response import Response, ResponseValue, make_response

NextHandler = Callable[[Request], ResponseValue]


class CORSMiddleware:
    def __init__(
        self,
        *,
        allow_origins: Iterable[str] = ("*",),
        allow_methods: Iterable[str] = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"),
        allow_headers: Iterable[str] = ("*",),
        expose_headers: Iterable[str] = (),
        allow_credentials: bool = False,
        max_age: int = 600,
    ) -> None:
        self.allow_origins = tuple(allow_origins)
        self.allow_methods = tuple(method.upper() for method in allow_methods)
        self.allow_headers = tuple(allow_headers)
        self.expose_headers = tuple(expose_headers)
        self.allow_credentials = allow_credentials
        self.max_age = max_age

    def __call__(self, request: Request, call_next: NextHandler) -> Response:
        response = make_response(call_next(request))
        origin = str(request.headers.get("origin", ""))
        allowed_origin = self._allowed_origin(origin)
        if allowed_origin is None:
            return response

        response.headers["access-control-allow-origin"] = allowed_origin
        if allowed_origin != "*":
            response.headers.setdefault("vary", "Origin")
        if self.allow_credentials:
            response.headers["access-control-allow-credentials"] = "true"
        if self.expose_headers:
            response.headers["access-control-expose-headers"] = ", ".join(self.expose_headers)
        if request.method == "OPTIONS":
            response.headers["access-control-allow-methods"] = ", ".join(self.allow_methods)
            response.headers["access-control-allow-headers"] = ", ".join(self.allow_headers)
            response.headers["access-control-max-age"] = str(self.max_age)
        return response

    def _allowed_origin(self, origin: str) -> str | None:
        if "*" in self.allow_origins and not self.allow_credentials:
            return "*"
        if origin in self.allow_origins:
            return origin
        return None


class SecurityHeadersMiddleware:
    def __init__(
        self,
        *,
        content_security_policy: str | None = "default-src 'self'",
        frame_options: str = "DENY",
        referrer_policy: str = "strict-origin-when-cross-origin",
    ) -> None:
        self.content_security_policy = content_security_policy
        self.frame_options = frame_options
        self.referrer_policy = referrer_policy

    def __call__(self, request: Request, call_next: NextHandler) -> Response:
        response = make_response(call_next(request))
        response.headers.setdefault("x-content-type-options", "nosniff")
        response.headers.setdefault("x-frame-options", self.frame_options)
        response.headers.setdefault("referrer-policy", self.referrer_policy)
        if self.content_security_policy:
            response.headers.setdefault(
                "content-security-policy", self.content_security_policy
            )
        return response


class TrustedHostMiddleware:
    def __init__(self, allowed_hosts: Iterable[str]) -> None:
        self.allowed_hosts = tuple(host.lower() for host in allowed_hosts)

    def __call__(self, request: Request, call_next: NextHandler) -> ResponseValue:
        host = str(request.headers.get("host", "")).split(":", 1)[0].lower()
        if host and not any(self._matches(host, pattern) for pattern in self.allowed_hosts):
            raise Forbidden(detail="untrusted Host header")
        return call_next(request)

    @staticmethod
    def _matches(host: str, pattern: str) -> bool:
        if pattern == "*":
            return True
        if pattern.startswith("*."):
            suffix = pattern[1:]
            return host.endswith(suffix) and host != suffix[1:]
        return host == pattern


def middleware(
    func: Callable[[Request, NextHandler], ResponseValue],
) -> Callable[[Request, NextHandler], ResponseValue]:
    return func
