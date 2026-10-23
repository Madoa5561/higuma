from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from .exceptions import MethodNotAllowed, NotFound
from .request import Request
from .response import FileResponse, RedirectResponse, Response


def _relative_parts(value: str) -> tuple[str, ...]:
    if (
        value.startswith("/")
        or "\\" in value
        or ":" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise NotFound()
    parts = tuple(value.split("/")) if value else ()
    if any(not part or part.startswith(".") for part in parts):
        raise NotFound()
    return parts


class StaticFiles:
    def __init__(
        self,
        directory: str | Path,
        *,
        html: bool = False,
        index_file: str = "index.html",
        max_age: int = 3600,
        immutable: bool = False,
        headers: Mapping[str, str] | None = None,
        check_dir: bool = True,
    ) -> None:
        if not isinstance(max_age, int) or isinstance(max_age, bool) or max_age < 0:
            raise ValueError("max_age must be a non-negative integer")
        try:
            if len(_relative_parts(index_file)) != 1:
                raise NotFound()
        except NotFound as error:
            raise ValueError("index_file must be a visible filename") from error
        self.directory = Path(directory).resolve()
        if check_dir and not self.directory.is_dir():
            raise ValueError(f"static directory does not exist: {self.directory}")
        self.html = html
        self.index_file = index_file
        self.max_age = max_age
        self.immutable = immutable
        self.headers = dict(Response(headers=headers).headers)

    def _path(self, filename: str) -> Path:
        parts = _relative_parts(filename)
        try:
            candidate = self.directory.joinpath(*parts).resolve()
        except (OSError, ValueError, RuntimeError) as error:
            raise NotFound() from error
        if not candidate.is_relative_to(self.directory):
            raise NotFound()
        if any(part.startswith(".") for part in candidate.relative_to(self.directory).parts):
            raise NotFound()
        return candidate

    def _response(self, path: Path, *, shell: bool = False, html: bool = False) -> FileResponse:
        cache = f"public, max-age={self.max_age}"
        if (
            shell
            or html
            or path.suffix.lower() in {".html", ".htm"}
            or path.name in {"sw.js", "service-worker.js"}
        ):
            cache = "no-cache"
        elif self.immutable:
            cache += ", immutable"
        headers = {"x-content-type-options": "nosniff", **self.headers}
        headers.setdefault("cache-control", cache)
        if shell:
            # A navigation may select HTML while an API-style fetch selects 404.
            vary = headers.get("vary", "")
            headers["vary"] = f"{vary}, Accept" if vary else "Accept"
            headers["cache-control"] = "no-cache"
        media_type = {
            ".js": "text/javascript; charset=utf-8",
            ".mjs": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".html": "text/html; charset=utf-8",
            ".htm": "text/html; charset=utf-8",
            ".wasm": "application/wasm",
            ".svg": "image/svg+xml",
            ".webmanifest": "application/manifest+json",
        }.get(path.suffix.lower())
        if shell or html:
            media_type = "text/html; charset=utf-8"
        return FileResponse(path, headers=headers, media_type=media_type)

    def __call__(self, request: Request, filename: str = "") -> Response:
        if request.method not in {"GET", "HEAD"}:
            raise MethodNotAllowed(headers={"allow": "GET, HEAD, OPTIONS"})
        candidate = self._path(filename)
        if candidate.is_dir() and self.html:
            candidate = self._path("/".join(filter(None, (filename, self.index_file))))
            if candidate.is_file() and not request.path.endswith("/"):
                query = f"?{request.query_string}" if request.query_string else ""
                return RedirectResponse(f"{request.path}/{query}", status=308)
        if not candidate.is_file():
            raise NotFound()
        return self._response(candidate, html=self.html and candidate.name == self.index_file)


class SPAFiles(StaticFiles):
    def __init__(
        self,
        directory: str | Path,
        *,
        index_file: str = "index.html",
        api_prefixes: Iterable[str] = ("/api",),
        asset_prefixes: Iterable[str] = ("/assets",),
        max_age: int = 3600,
        immutable: bool = False,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(
            directory, index_file=index_file, max_age=max_age, immutable=immutable, headers=headers
        )
        self.api_prefixes = _prefixes(api_prefixes)
        self.asset_prefixes = _prefixes(asset_prefixes)
        try:
            entry_exists = self._path(index_file).is_file()
        except NotFound as error:
            raise ValueError("SPA entry must be inside the public directory") from error
        if not entry_exists:
            raise ValueError(f"SPA entry file does not exist: {index_file}")

    def __call__(self, request: Request, filename: str = "") -> FileResponse:
        if request.method not in {"GET", "HEAD"}:
            raise MethodNotAllowed(headers={"allow": "GET, HEAD, OPTIONS"})
        candidate = self._path(filename)
        relative_url = f"/{filename}"
        if _matches_prefix(relative_url, self.api_prefixes):
            raise NotFound()
        if candidate.is_file():
            return self._response(candidate, shell=candidate == self._path(self.index_file))
        if (
            any("." in part for part in _relative_parts(filename))
            or _matches_prefix(relative_url, self.asset_prefixes)
            or not _accepts_html(request.headers.get("accept"))
        ):
            raise NotFound()
        shell = self._path(self.index_file)
        if not shell.is_file():
            raise NotFound()
        return self._response(shell, shell=True)


def _prefixes(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, str):
        raise TypeError("prefixes must be an iterable of paths, not one string")
    result = []
    for value in values:
        if not value.startswith("/") or value.startswith("//"):
            raise ValueError("prefixes must be root-relative paths")
        try:
            _relative_parts(value.strip("/"))
        except NotFound as error:
            raise ValueError("prefixes must be literal paths without dot segments") from error
        if any(char in value for char in "?#%<>"):
            raise ValueError("prefixes must be literal paths")
        result.append(value.rstrip("/") or "/")
    return tuple(result)


def _matches_prefix(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(
        prefix == "/" or path == prefix or path.startswith(prefix + "/") for prefix in prefixes
    )


def _accepts_html(value: str | None) -> bool:
    if value is None:
        return True
    matches = []
    for item in value.lower().split(","):
        media_type, *parameters = item.strip().split(";")
        if media_type not in {"text/html", "text/*", "*/*"}:
            continue
        quality = 1.0
        for parameter in parameters:
            name, _, text = parameter.strip().partition("=")
            if name == "q":
                try:
                    quality = float(text)
                except ValueError:
                    quality = 0.0
                if not 0 <= quality <= 1:
                    quality = 0.0
        specificity = {"text/html": 2, "text/*": 1, "*/*": 0}[media_type]
        matches.append((specificity, quality))
    if not matches:
        return False
    most_specific = max(specificity for specificity, _ in matches)
    return any(quality > 0 for specificity, quality in matches if specificity == most_specific)
