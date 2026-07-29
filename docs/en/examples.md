# Examples

The repository includes runnable examples:

| File | Covers |
| --- | --- |
| `app.py` | Routing, JSON, SSR |
| `websocket_chat.py` | JSON WebSocket echo |
| `multipart_upload.py` | File upload and persistence |
| `mounted_apps.py` | WSGI and ASGI mounts |
| `openapi_api.py` | Dataclasses and OpenAPI |
| `orm_blog.py` | SQLite ORM CRUD |
| `authentication.py` | Login, CSRF, passwords |
| `oauth_login.py` | Google OAuth flow |
| `security_hardened.py` | CORS, hosts, headers, limits |
| `supervised_app.py` | Multi-process supervisor |

```bash
cd examples
python websocket_chat.py
python multipart_upload.py
higuma run supervised_app:app --processes 4
```

[Browse examples on GitHub](https://github.com/Madoa5561/higuma/tree/main/examples)
