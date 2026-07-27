# Contributing to higuma

Thank you for helping improve higuma.

## Development setup

Requirements:

- Python 3.10 or newer
- Rust stable toolchain
- A C/C++ linker supported by Rust

```bash
python -m venv .venv
.venv/Scripts/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

On macOS or Linux, activate the environment with:

```bash
source .venv/bin/activate
```

## Checks

Run all checks before opening a pull request:

```bash
cargo fmt --all -- --check
cargo check
python -m ruff check python tests examples
python -m unittest discover -s tests -v
```

## Pull requests

- Keep changes focused and explain the behavior they change.
- Add tests for bug fixes and new public behavior.
- Update README.md and CHANGELOG.md when public APIs change.
- Avoid claiming benchmark improvements without a reproducible command and environment.

## Design principles

- Python APIs should be familiar and explicit.
- Work that does not require Python should stay in Rust when practical.
- Security-sensitive defaults should be conservative.
- Backwards compatibility follows Semantic Versioning after 1.0.
