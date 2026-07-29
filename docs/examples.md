# Examples

リポジトリの`examples/`には次の実行例があります。

| File | 内容 |
| --- | --- |
| `app.py` | routing、JSON、SSR |
| `websocket_chat.py` | JSON WebSocket echo |
| `multipart_upload.py` | file uploadと保存 |
| `mounted_apps.py` | WSGI / ASGI mount |
| `openapi_api.py` | dataclassとOpenAPI |
| `orm_blog.py` | SQLite ORM CRUD |
| `authentication.py` | login、CSRF、password |
| `oauth_login.py` | Google OAuth flow |
| `security_hardened.py` | CORS、host、header、rate limit |
| `supervised_app.py` | multi-process supervisor |

```bash
cd examples
python websocket_chat.py
python multipart_upload.py
higuma run supervised_app:app --processes 4
```

[examplesをGitHubで開く](https://github.com/Madoa5561/higuma/tree/main/examples)
