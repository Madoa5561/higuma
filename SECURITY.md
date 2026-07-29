# Security Policy

## Supported versions

Security fixes are provided for the latest released minor version.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's private
security advisory feature after this repository is published.

Include:

- Affected version and operating system
- Minimal reproduction steps
- Expected impact
- Any known workaround

Please allow maintainers reasonable time to validate and prepare a coordinated
fix before public disclosure.

## Production guidance

- Keep `debug=False` outside local development.
- Set a random secret containing at least 32 bytes and use secure cookies
  behind HTTPS.
- Configure `TrustedHostMiddleware` for public deployments.
- Configure `ProxyHeadersMiddleware` with exact direct proxy IPs or CIDRs;
  forwarded headers are ignored otherwise.
- Keep WebSocket origins same-origin or configure exact `allowed_origins`.
- Put a mature reverse proxy in front of higuma for TLS, request buffering, and
  network-level limits.
- Review CORS and Content Security Policy values for each application.

## Automated checks

- CI runs Python and Rust tests on Linux, Windows, and macOS.
- Ruff formatting and lint plus Clippy warnings are release-blocking.
- Dependabot monitors Cargo, Python, and GitHub Actions dependencies weekly.
