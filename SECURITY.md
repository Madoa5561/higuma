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
- Set a strong random session secret and use secure cookies behind HTTPS.
- Configure `TrustedHostMiddleware` for public deployments.
- Put a mature reverse proxy in front of higuma for TLS, request buffering, and
  network-level limits.
- Review CORS and Content Security Policy values for each application.
