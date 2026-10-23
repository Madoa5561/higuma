from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit


class ViteManifest:
    def __init__(self, path: str | Path, *, base_url: str = "/static/") -> None:
        self.path = Path(path)
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"", "http", "https"}
            or (parsed.scheme and (not parsed.netloc or not parsed.hostname))
            or (not parsed.scheme and (parsed.netloc or not base_url.startswith("/")))
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or "\\" in base_url
            or any(ord(char) < 32 or ord(char) == 127 for char in base_url)
            or any(part in {".", ".."} for part in parsed.path.split("/"))
            or "%" in base_url
            or (parsed.netloc and parsed.port is not None and not 0 <= parsed.port <= 65535)
        ):
            raise ValueError("base_url must be a root-relative path or absolute HTTP(S) URL")
        self.base_url = base_url.rstrip("/") + "/"
        self.reload()

    def reload(self) -> None:
        manifest = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("Vite manifest must be an object")  # noqa: TRY004 - invalid file data
        for key, chunk in manifest.items():
            if not isinstance(chunk, dict) or not isinstance(chunk.get("file"), str):
                raise ValueError(f"invalid Vite manifest chunk: {key}")  # noqa: TRY004 - file data
            self._url(chunk["file"])
            for field in ("css", "imports", "dynamicImports", "assets"):
                values = chunk.get(field, [])
                if not isinstance(values, list) or not all(
                    isinstance(value, str) for value in values
                ):
                    raise ValueError(f"manifest field {key}.{field} must be a list of strings")
                if field in {"css", "assets"}:
                    for value in values:
                        self._url(value)
        for chunk in manifest.values():
            for key in (*chunk.get("imports", []), *chunk.get("dynamicImports", [])):
                if key not in manifest:
                    raise ValueError(f"missing imported manifest chunk: {key}")
        self._manifest: dict[str, Any] = manifest

    def _url(self, filename: str) -> str:
        if (
            not filename
            or "\\" in filename
            or ":" in filename
            or any(char in filename for char in "?#%")
            or any(not part or part.startswith(".") for part in filename.split("/"))
            or any(ord(char) < 32 or ord(char) == 127 for char in filename)
        ):
            raise ValueError("manifest assets must be visible relative file paths")
        return self.base_url + quote(filename, safe="/")

    def asset_url(self, entry: str) -> str:
        return self._url(self._manifest[entry]["file"])

    def entrypoint(self, entry: str) -> dict[str, Any]:
        manifest = self._manifest
        root = manifest[entry]
        seen = {entry}
        dependencies = []
        stack = [(key, False) for key in reversed(root.get("imports", []))]
        while stack:
            key, expanded = stack.pop()
            if expanded:
                dependencies.append(manifest[key])
                continue
            if key in seen:
                continue
            if key not in manifest:
                raise ValueError(f"missing imported manifest chunk: {key}")
            seen.add(key)
            stack.append((key, True))
            stack.extend((child, False) for child in reversed(manifest[key].get("imports", [])))
        styles = list(root.get("css", []))
        preloads = []
        for chunk in dependencies:
            styles.extend(chunk.get("css", []))
            if chunk["file"].endswith(".css"):
                styles.append(chunk["file"])
            elif chunk["file"].endswith((".js", ".mjs")):
                preloads.append(chunk["file"])
        return {
            "file": self._url(root["file"]),
            "css": [self._url(value) for value in dict.fromkeys(styles)],
            "imports": [self._url(value) for value in dict.fromkeys(preloads)],
        }

    def tags(self, entry: str, *, nonce: str | None = None) -> str:
        urls = self.entrypoint(entry)
        nonce_attribute = f' nonce="{escape(nonce, quote=True)}"' if nonce is not None else ""
        styles = list(urls["css"])
        filename = urls["file"]
        if filename.endswith(".css"):
            styles.append(urls["file"])
        elif not filename.endswith((".js", ".mjs")):
            raise ValueError(
                "tags requires a JavaScript or CSS entry; use asset_url for other files"
            )
        tags = [
            f'<link rel="stylesheet" href="{escape(url, quote=True)}">'
            for url in dict.fromkeys(styles)
        ]
        if filename.endswith((".js", ".mjs")):
            tags.append(
                f'<script type="module" src="{escape(urls["file"], quote=True)}"'
                f"{nonce_attribute}></script>"
            )
        tags.extend(
            f'<link rel="modulepreload" crossorigin href="{escape(url, quote=True)}">'
            for url in urls["imports"]
        )
        return "\n".join(tags)
