from __future__ import annotations

from collections.abc import Callable, Iterable
from ipaddress import ip_address, ip_network

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
        if self.allow_credentials and "*" in self.allow_origins:
            raise ValueError("credentialed CORS requires explicit origins")

    def __call__(self, request: Request, call_next: NextHandler) -> Response:
        response = make_response(call_next(request))
        origin = str(request.headers.get("origin", ""))
        allowed_origin = self._allowed_origin(origin)
        if allowed_origin is None:
            return response

        response.headers["access-control-allow-origin"] = allowed_origin
        if allowed_origin != "*":
            _add_vary(response, "Origin")
        if self.allow_credentials:
            response.headers["access-control-allow-credentials"] = "true"
        if self.expose_headers:
            response.headers["access-control-expose-headers"] = ", ".join(self.expose_headers)
        if request.method == "OPTIONS":
            requested_method = str(request.headers.get("access-control-request-method", "")).upper()
            if requested_method and requested_method not in self.allow_methods:
                raise Forbidden(detail="CORS method is not allowed")
            requested_headers = {
                value.strip().lower()
                for value in str(request.headers.get("access-control-request-headers", "")).split(
                    ","
                )
                if value.strip()
            }
            allowed_headers = {value.lower() for value in self.allow_headers}
            if "*" not in allowed_headers and not requested_headers <= allowed_headers:
                raise Forbidden(detail="CORS headers are not allowed")
            response.headers["access-control-allow-methods"] = ", ".join(self.allow_methods)
            response.headers["access-control-allow-headers"] = (
                ", ".join(sorted(requested_headers))
                if "*" in allowed_headers and requested_headers
                else ", ".join(self.allow_headers)
            )
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
        permissions_policy: str | None = "camera=(), microphone=(), geolocation=()",
        strict_transport_security: str | None = None,
    ) -> None:
        self.content_security_policy = content_security_policy
        self.frame_options = frame_options
        self.referrer_policy = referrer_policy
        self.permissions_policy = permissions_policy
        self.strict_transport_security = strict_transport_security

    def __call__(self, request: Request, call_next: NextHandler) -> Response:
        response = make_response(call_next(request))
        response.headers.setdefault("x-content-type-options", "nosniff")
        response.headers.setdefault("x-frame-options", self.frame_options)
        response.headers.setdefault("referrer-policy", self.referrer_policy)
        response.headers.setdefault("cross-origin-opener-policy", "same-origin")
        if self.content_security_policy:
            response.headers.setdefault("content-security-policy", self.content_security_policy)
        if self.permissions_policy:
            response.headers.setdefault("permissions-policy", self.permissions_policy)
        if self.strict_transport_security:
            response.headers.setdefault(
                "strict-transport-security",
                self.strict_transport_security,
            )
        return response


class TrustedHostMiddleware:
    def __init__(self, allowed_hosts: Iterable[str]) -> None:
        self.allowed_hosts = tuple(host.lower() for host in allowed_hosts)

    def __call__(self, request: Request, call_next: NextHandler) -> ResponseValue:
        raw_host = str(request.headers.get("host", "")).lower()
        host = (
            raw_host[1 : raw_host.find("]")]
            if raw_host.startswith("[") and "]" in raw_host
            else raw_host.split(":", 1)[0]
        )
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


class ProxyHeadersMiddleware:
    def __init__(
        self,
        trusted_proxies: Iterable[str] = ("127.0.0.1", "::1"),
    ) -> None:
        self.trusted_proxies = tuple(ip_network(value, strict=False) for value in trusted_proxies)

    def __call__(self, request: Request, call_next: NextHandler) -> ResponseValue:
        try:
            peer = ip_address(request.client_addr)
        except ValueError:
            return call_next(request)
        if not any(peer in network for network in self.trusted_proxies):
            return call_next(request)

        forwarded_for = str(request.headers.get("x-forwarded-for", ""))
        chain = []
        for value in forwarded_for.split(","):
            try:
                chain.append(ip_address(value.strip()))
            except ValueError:
                continue
        chain.append(peer)
        client = next(
            (
                address
                for address in reversed(chain)
                if not any(address in network for network in self.trusted_proxies)
            ),
            chain[0],
        )
        request.client_addr = str(client)
        request.remote_addr = request.client_addr

        forwarded_proto = (
            str(request.headers.get("x-forwarded-proto", "")).rsplit(",", 1)[-1].strip()
        )
        if forwarded_proto.lower() in {"http", "https"}:
            request.scheme = forwarded_proto.lower()
        return call_next(request)


def _add_vary(response: Response, value: str) -> None:
    existing = {
        item.strip().lower() for item in response.headers.get("vary", "").split(",") if item.strip()
    }
    if value.lower() not in existing:
        response.headers["vary"] = ", ".join(
            [item for item in (response.headers.get("vary"), value) if item]
        )


def middleware(
    func: Callable[[Request, NextHandler], ResponseValue],
) -> Callable[[Request, NextHandler], ResponseValue]:
    return func
