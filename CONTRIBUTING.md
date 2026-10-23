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
python -m pip install -e ".[dev,docs,compat,security]"
```

On macOS or Linux, activate the environment with:

```bash
source .venv/bin/activate
```

## Checks

Run all checks before opening a pull request:

```bash
cargo fmt --all -- --check
cargo clippy --locked --all-targets --all-features -- -D warnings
cargo test --locked --all-features
python -m ruff format --check python tests examples tools
python -m ruff check python tests examples tools
python -m pytest
python tools/verify_release.py
mkdocs build --strict
python -m pip list --format freeze --exclude higuma > audit-requirements.txt
python -m pip_audit -r audit-requirements.txt --no-deps --disable-pip --strict --progress-spinner off
cargo audit
```

Install `cargo-audit` with `cargo install cargo-audit --locked --version 0.22.2`.
Audits access public advisory databases; they do not establish that the code has
no vulnerabilities. CI includes real Flask compatibility tests and real-socket
HTTP/WebSocket tests. `unittest discover` alone does not run the full pytest suite.
Editable higuma itself is excluded from the advisory lookup (it may not have been
published yet); its source is covered by review/tests, not a PyPI version lookup.
The generated `audit-requirements.txt` is an audit snapshot, not a runtime lockfile.

Before tagging a release, push the exact commit and wait for both CI and Dependency
security to succeed. The release workflow refuses stale-success-only results.

## Frontend verification

```bash
python -m pytest tests/test_frontend.py tests/test_csr_server.py
node --check examples/frontend/assets/app.js
```

Node is optional for the Python runtime; the syntax check runs on the hosted CI
image. The CSR example itself needs only a browser and higuma. Start
`higuma run examples.csr_app:create_app`, open `/app/`, navigate to the API page,
reload `/app/status`, then navigate and use Back. Confirm real JSON, no browser
errors, and 404 for missing assets/reserved APIs. For a complete integration test,
build your own Vite project too; manifest fixtures do not prove every compiler,
plugin, browser, or framework combination.

Symlink tests may skip on Windows without symlink privileges; do not change OS
security settings just to run them. If an existing pytest temporary root is
inaccessible, use a new, task-specific `--basetemp` under ignored `target/` rather
than deleting or changing permissions on another task's temporary directory.

## Pull request checklist

- Keep changes focused and explain the behavior they change.
- Add tests for bug fixes and new public behavior.
- Update README.md and CHANGELOG.md when public APIs change.
- Avoid claiming benchmark improvements without a reproducible command and environment.

## Design principles

- Python APIs should be familiar and explicit.
- Work that does not require Python should stay in Rust when practical.
- Security-sensitive defaults should be conservative.
- Backwards compatibility follows Semantic Versioning after 1.0.
