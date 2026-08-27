from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _python_version() -> str:
    tree = ast.parse((ROOT / "python/higuma/app.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__version__"
                for target in node.targets
            )
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    raise RuntimeError("python/higuma/app.py does not define a string __version__")


def _toml_version(path: str, table: str) -> str:
    text = (ROOT / path).read_text(encoding="utf-8")
    table_match = re.search(
        rf"^\[{re.escape(table)}\]\s*$(.*?)(?=^\[|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if table_match is None:
        raise RuntimeError(f"{path} has no [{table}] table")
    version_match = re.search(r'^version\s*=\s*"([^"]+)"\s*$', table_match.group(1), re.MULTILINE)
    if version_match is None:
        raise RuntimeError(f'{path} [{table}] has no string "version" field')
    return version_match.group(1)


def _lock_version() -> str:
    text = (ROOT / "Cargo.lock").read_text(encoding="utf-8")
    matches = re.findall(
        r'^\[\[package\]\]\s*$\s*^name\s*=\s*"higuma"\s*$\s*'
        r'^version\s*=\s*"([^"]+)"\s*$',
        text,
        re.MULTILINE,
    )
    if len(matches) != 1:
        raise RuntimeError("Cargo.lock must contain exactly one higuma package")
    return str(matches[0])


def versions() -> dict[str, str]:
    return {
        "pyproject.toml": _toml_version("pyproject.toml", "project"),
        "Cargo.toml": _toml_version("Cargo.toml", "package"),
        "Cargo.lock": _lock_version(),
        "python/higuma/app.py": _python_version(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify higuma release version alignment")
    parser.add_argument("--tag", help="release tag in vX.Y.Z form")
    args = parser.parse_args()

    found = versions()
    unique = set(found.values())
    if len(unique) != 1:
        details = ", ".join(f"{path}={version}" for path, version in found.items())
        raise SystemExit(f"version mismatch: {details}")

    version = unique.pop()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit(f"version must use X.Y.Z semantic versioning: {version}")

    if args.tag:
        if not re.fullmatch(r"v\d+\.\d+\.\d+", args.tag):
            raise SystemExit(f"release tag must use vX.Y.Z: {args.tag}")
        if args.tag != f"v{version}":
            raise SystemExit(f"tag {args.tag} does not match package version {version}")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        if (
            re.search(
                rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$",
                changelog,
                re.MULTILINE,
            )
            is None
        ):
            raise SystemExit(f"CHANGELOG.md has no dated {version} release section")

    print(f"higuma version {version} is aligned across all release files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
