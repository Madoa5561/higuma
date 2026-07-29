import os

from higuma import (
    CORSMiddleware,
    Higuma,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    TrustedHostMiddleware,
)

app = Higuma(__name__)
app.add_middleware(
    TrustedHostMiddleware,
    ("localhost", "127.0.0.1", os.environ.get("APP_HOST", "example.com")),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=(os.environ.get("APP_ORIGIN", "https://example.com"),),
    allow_credentials=True,
)
app.add_middleware(
    SecurityHeadersMiddleware,
    content_security_policy="default-src 'self'; object-src 'none'; frame-ancestors 'none'",
)
app.add_middleware(RateLimitMiddleware, limit=120, window=60)


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(host="0.0.0.0")
