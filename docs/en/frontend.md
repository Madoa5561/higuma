# CSR, SPA, and frontend integration

These APIs require 0.6.0 or later. Version 0.5.0 does not provide `mount_spa`,
`mount_static`, or `ViteManifest`. Upgrade with `python -m pip install -U higuma`.
For running or developing the examples, obtain the repository and install it in editable mode.

## Choose a rendering model

| Model | Rendering owner | Higuma integration |
| --- | --- | --- |
| SSR | Python views and MiniJinja generate HTML | `app.render_template()` |
| CSR / SPA | Browser JavaScript renders and navigates after loading the shell | `mount_spa()` and APIs |
| Python HTML + Vite assets | Python renders HTML, browser executes bundles | `ViteManifest` and `mount_static()` |
| Static generation / MPA | An external builder generates multiple HTML pages | `mount_static(..., html=True)` |

Higuma does not embed a React/Vue/Svelte compiler, Node runtime, or React component
SSR engine. It serves their build output alongside Python authentication, APIs, and data access.

## Run the dependency-free CSR example

```bash
python -m pip install -e ".[dev]"
higuma run examples.csr_app:create_app --port 8000
```

Open `http://127.0.0.1:8000/app/`. The API status page fetches real JSON from
`GET /api/status`. Direct navigation to `/app/status`, reload, and browser Back
work with ES modules, History API, and fetch, without npm dependencies.

## Serve a built SPA

```python
from higuma import Higuma

app = Higuma(__name__, static_folder=None)

@app.get("/api/hello")
def hello():
    return {"message": "hello"}

app.mount_spa("/", "frontend/dist")
```

The directory must contain `index.html`; missing directories/entry files fail at
registration with `ValueError`. Relative directories resolve against the process
working directory. Pass `Path(__file__).parent / "frontend/dist"` for module-relative paths.

For a subpath, use `app.mount_spa("/app", "frontend/dist", name="client")` and
configure the frontend asset base and router basename to `/app/`. Moving a build
that references `/assets/...` into `/app/` does not rewrite its generated URLs.

### History fallback rules

- Only GET/HEAD are served. Explicit matching application routes take precedence;
  a GET to a known POST-only route remains 405, including its OPTIONS contract.
- Existing public files are served directly. No directory listing is provided.
- Missing extensionless paths may return the HTML shell. Paths with dots in any
  segment do not fall back to HTML.
- `api_prefixes=("/api",)` reserves API paths. `asset_prefixes=("/assets",)` serves
  real files but never falls back, even for extensionless missing assets. Prefixes
  are relative to the mount and matched at segment boundaries.
- Accept values excluding HTML (such as `application/json` or `text/html;q=0`)
  disable fallback. Missing Accept and `*/*` allow it; keep APIs in reserved
  prefixes or explicit routes.
- Responses produced by your application, including 401/403/404/500, are never
  rewritten into an SPA page.
- Dotfiles, dot segments, backslashes, drive/ADS syntax, and symlink escapes are rejected.

```python
app.mount_spa(
    "/app", "frontend/dist", name="client",
    api_prefixes=("/api", "/graphql"),
    asset_prefixes=("/assets", "/fonts", "/images"),
)
```

Conflicting GET paths or endpoint names fail before registration. Give each mount
a unique `name`; `url_for("client_root")` and
`url_for("client_path", filename="assets/main.js")` generate mount URLs.

## Multiple static roots and static HTML

```python
app.mount_static("/media", "public/media", name="media", max_age=3600)
app.mount_static("/help", "public/help", name="help", html=True)
app.mount_static("/assets", "frontend/dist/assets", name="bundles",
                 max_age=31536000, immutable=True)
```

`html=True` serves directory `index.html` files and redirects slashless directory
URLs with HTTP 308, preserving the query. Without HTML mode, directories return
404. `index_file` accepts another visible filename. Hidden files and escapes are
always denied.

Use `immutable=True` only for content-addressed/hashed asset URLs. Replacing content
at a long-lived immutable URL prevents clients from seeing updates. HTML and the
standard `sw.js`/`service-worker.js` filenames default to `no-cache`. SPA shells
always use `no-cache` and `Vary: Accept`. Other files default to
`public, max-age=3600` and `X-Content-Type-Options: nosniff`.

File bodies stream from Rust. Whole-file `FileResponse` and `send_file()` use the
same GET/HEAD conditional handling:

| Request header | Behavior |
| --- | --- |
| `If-None-Match` | ETag lists, weak comparison, and `*`; 304 when unchanged |
| `If-Modified-Since` | 304 when unchanged; `If-None-Match` takes precedence |
| `If-Match` | Strong comparison; 412 on mismatch |
| `If-Unmodified-Since` | 412 when newer; `If-Match` takes precedence |
| `Range: bytes=...` | 206 for one valid range; 416 for unsatisfiable or multiple ranges |
| `If-Range` | 206 only for the current strong ETag or exact Last-Modified date; otherwise full 200 |

Validators are generated from file metadata, not a cryptographic content signature.
Implement optimistic concurrency for write APIs separately.

## Vite development and production

### Development

Run the frontend through Vite and proxy `/api` to Higuma. Merge this into an existing
Vite project's configuration:

```javascript
import { defineConfig } from 'vite';

export default defineConfig(({ command }) => ({
  base: command === 'serve' ? '/' : '/app/',
  server: {
    host: '127.0.0.1',
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
  build: { manifest: true },
}));
```

HMR belongs to Vite. Higuma does not start Node or proxy Vite's WebSocket. Open the
frontend development URL at its root locally and the Higuma `/app/` URL in production.
Derive the router base from `import.meta.env.BASE_URL`, or otherwise keep both modes
aligned. This example uses a root base in development to match the `/api` proxy.
If you keep a nonroot development base, follow [Vite's proxy rules](https://vite.dev/config/server-options#server-proxy)
and prefix the proxy keys and client API URLs accordingly. Do not
expose a development server with unrestricted CORS. For a normal built SPA, simply
mount its generated HTML; the manifest helper below is optional.

### HTML rendered by Python

Vite's `build.manifest: true` produces `.vite/manifest.json`. Load its snapshot to
resolve hashed files, CSS dependencies, and module preloads:

```python
from higuma import ViteManifest

vite = ViteManifest("frontend/dist/.vite/manifest.json", base_url="/build/")
logo_url = vite.asset_url("src/logo.svg")  # use a key present in your manifest
tags = vite.tags("index.html", nonce="request-specific-csp-nonce")
```

Set the Vite base and `base_url` to `/build/` and serve the build with
`app.mount_static("/build", "frontend/dist")`. The runnable factory
`examples/vite_ssr.py` reads `HIGUMA_FRONTEND_DIST` (default `frontend/dist`). It
server-renders Python HTML, not React components.

```python
return app.render_template("vite.html", vite_tags=vite.tags("index.html"))
```

```html
<!-- Only pass HTML generated by this trusted helper. -->
{{ vite_tags | safe }}
```

- `asset_url(entry)` returns one output URL; unknown keys raise `KeyError`.
- `entrypoint(entry)` returns `file`, `css`, and `imports` URLs.
- `tags(entry, nonce=None)` emits entry CSS, dependency CSS, module script, and
  modulepreload tags in order. CSS/imports are deduplicated; cycles terminate.
  CSS-only entries are supported.
- Dynamic imports remain lazy. Use `asset_url()` for images and other assets.
- URLs and nonces are HTML-escaped. Nonrelative asset paths and unsafe base URLs are rejected.
- Loading occurs at construction or explicit `reload()`, not on every request.
  Failed reloads keep the previous snapshot. Deploy atomically and retain old
  hashed assets while existing browser tabs may still need them.
- Node rendering, React Refresh preambles, and experimental chunk import maps
  are not inserted automatically.

## Authentication and production boundaries

CSR still needs [sessions and CSRF protection](security-auth.md). For cookie-based
mutation APIs, obtain a CSRF token first and send `X-CSRF-Token` on POST/PATCH/DELETE.
Never put secrets, OAuth client secrets, or database credentials in bundles/public roots.

For separate origins, use exact `CORSMiddleware` origins, enable credentials only
when required, and use `credentials: 'include'` in fetch. CORS does not replace
authentication or CSRF protection. Configure SameSite, Secure cookies, HTTPS,
trusted hosts/proxies, and WebSocket origins appropriately.

- User-specific APIs/tokens should use `Cache-Control: no-store`, not shared public caches.
- Serve build output only: no sources, secrets, databases, or server-side upload stores.
- Unknown client-side screens still get an HTTP200 shell. Use SSR/static generation
  when SEO or exact HTTP404 status is required.
- Deploy atomically and retain old lazy chunks to avoid breaking stale tabs.
- Service workers, offline cache lifecycle, and web manifests belong to the frontend;
  Higuma does not automatically turn a site into a PWA.
- Precompressed Brotli negotiation, CDN purge, distributed caches, and durable queues
  belong to external infrastructure.

References: [Vite backend integration](https://vite.dev/guide/backend-integration),
[React Router SPA](https://reactrouter.com/how-to/spa). See the
[capability matrix](capabilities.md) for the broader framework boundary.
