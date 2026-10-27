# Contributing to higuma

Thank you for helping improve higuma.

## Development setup

Requirements:

- Python 3.10 or newer for the runtime; use Python 3.14 for the development/audit commands below
- Rust stable toolchain
- A C/C++ linker supported by Rust

```bash
python -m venv .venv
.venv/Scripts/activate
python -m pip install -U pip "setuptools>=84.1" "filelock>=4.2.1"
python -m pip install -e ".[dev,docs,compat,security,asgi,integrations,benchmark,async-db,observability]"
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
python -m ruff format --check python tests examples tools bench
python -m ruff check python tests examples tools bench
python -m pytest
python tools/verify_release.py
mkdocs build --strict
python -m pip list --format freeze --exclude higuma > audit-requirements.txt
python -m pip_audit -r audit-requirements.txt --no-deps --disable-pip --strict --progress-spinner off
cargo audit --deny unsound
```

Install `cargo-audit` with `cargo install cargo-audit --locked --version 0.22.2`.
CI resolves runtime extras using Python 3.10/3.14 in separate pip-free environments,
then audits both runtime inventories and patched Python 3.14 tooling. Do not install
unpatched legacy audit-tool dependencies merely to audit an older supported runtime.
Audits access public advisory databases; they do not establish that the code has
no vulnerabilities. `unsound` warnings fail the Rust audit as well as known
vulnerabilities; do not treat a warning-only successful run as a clean audit.
CI includes real Flask compatibility tests and real-socket
HTTP/WebSocket tests. `unittest discover` alone does not run the full pytest suite.
Editable higuma itself is excluded from the advisory lookup (it may not have been
published yet); its source is covered by review/tests, not a PyPI version lookup.
The generated `audit-requirements.txt` is an audit snapshot, not a runtime lockfile.

Before tagging a release, push the exact commit and wait for both CI and Dependency
security to succeed. The release workflow refuses stale-success-only results.

## External server interface verification

```bash
python -m pytest tests/test_gateways.py tests/test_gateways_server.py
```

The `asgi` extra is optional for runtime users and installed by CI. Validate
WSGI with the standard validator and real wsgiref; validate ASGI with protocol
messages plus real Uvicorn HTTP/WS/SSE. Cover same-loop lifespan/resources,
ContextVar/yield cleanup, cancellation, backpressure, bounded messages, cookies,
root paths, and middleware boundaries. No native-performance claim applies
automatically to an external server. See docs/server-interfaces.md.

## Flask compatibility regression tests

```bash
python -m pytest tests/test_flask_helpers.py tests/test_flask_helpers_server.py
```

With the optional `compat` extra, shared cases run against real Flask as well as
Higuma. Keep intentional differences documented rather than hiding them behind
skips. No Flask async extra is needed: async-specific cases test Higuma directly.
The real-HTTP suite exercises the lifecycle example, CSRF, cookie persistence,
Unicode JSON, scoped hooks, and teardown. Context-only success does not prove
middleware/session behavior. Read docs/testing.md (English under docs/en/).

## Frontend and browser verification

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
