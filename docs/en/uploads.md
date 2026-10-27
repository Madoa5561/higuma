# Large bodies and uploads (0.9+)

Native, ASGI, and WSGI ingress reads chunks and spools bodies larger than the
default 1MiB to temporary files. The default total-body limit remains 8MiB.
Storage threshold and accepted size are separate limits.

```python
from higuma import Higuma, request

app = Higuma(__name__, max_content_length=64 * 1024 * 1024,
             request_memory_limit=1024 * 1024)

@app.post("/upload")
def upload():
    return {"size": sum(len(chunk) for chunk in request.iter_body()),
            "spooled": request.body_spooled}
```

## Reading

- `request.stream`: rewindable binary file-like with `read(size)`, `seek`, `tell`.
- `iter_body(chunk_size=65536)`: reads from the current cursor. Seek to zero before rereading.
- `aiter_body(chunk_size=65536)`: async stored-body iteration with disk reads off-loop.
- `body_size` / `content_length`: actual received size, without materializing body bytes.
- `body_spooled`: whether ingress storage rolled to disk, not individual file storage.
- `body/get_data/text/json` remain compatible but can materialize the full input in memory.

**Dispatch still begins after ingress completes.** These iterators read stored
input, not live network chunks while a client is uploading. For live ingress,
route directly to an external ASGI application without a buffered Higuma mount.

## Multipart

Read files incrementally with `request.files["file"].read(65536)`. Boundaries can
span chunks; files spool individually. `UploadFile.read/seek/save/getvalue` remain,
but `getvalue()` reads everything. Sanitized filenames are not collision protection:
`save()` can overwrite a target, so assign server-side unique names and protected
storage paths when persisting uploads.

| Config | Default | Scope |
| --- | --- | --- |
| `MAX_CONTENT_LENGTH` | 8MiB | Entire body; native uses constructor value |
| `REQUEST_BODY_MEMORY_LIMIT` | 1MiB | Ingress threshold; native uses constructor `request_memory_limit` |
| `MAX_FORM_MEMORY_SIZE` | 1MiB | Combined non-file multipart fields / whole URL-encoded form |
| `MAX_FORM_PARTS` | 1000 | Multipart parts |
| `UPLOAD_MEMORY_LIMIT` | 1MiB | Each multipart file threshold |

Values must be positive. Preamble and part headers are also limited to 64KiB.
Browser-style binary/8bit multipart is supported; base64 and other transfer
encodings are rejected. Oversize input gives 413, invalid/incomplete multipart 400.

## Cleanup and operations

Files close after normal responses, streams, disconnects, or parse failures.
Required request resources survive response/background/dependency work. Do not
retain UploadFile outside its request; copy it into owned storage before responding.

Standard temporary directories are used. Configure disk quota/free space,
permissions/encryption, connection limits, and ingress timeouts operationally.
Spooling does not replace a total size limit. Input spools and multipart file
spools can temporarily duplicate disk usage. Upstream server/proxy buffering is
outside this adapter's control.

`examples/server_modes.py` exposes `/upload` with size/SHA-256 verification.
Real-socket tests cover raw/multipart inputs above 2MiB in all three server modes.
