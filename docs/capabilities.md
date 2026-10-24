# 機能対応表と設計上の境界

HigumaはRust HTTP coreとPython APIを組み合わせた小さなframeworkです。
他frameworkに存在するすべての機能を内蔵した、とは表現しません。
各機能を**内蔵**・**連携**・**外部で構成**に分けます。0.6.0で追加した機能は表内に明記しています。

| 分野 | 提供方法 | 対応範囲／次に読むページ |
| --- | --- | --- |
| Routing / Blueprint / class view | 内蔵 | typed path、HTTP method、hooks、MethodView。[機能ガイド](features.md) |
| Input validation / DI / OpenAPI | 内蔵 | Annotated、dataclass、TypedDict、yield cleanup、output filtering。[API](api.md) |
| SSR | 内蔵 | MiniJinjaでHTML生成。Jinja2全機能やPython callable globalの完全互換ではない |
| CSR / SPA / history router | 内蔵（0.6） | safe fallback・API予約・subpath。[frontend guide](frontend.md) |
| Vite / React / Vue / Svelte | 連携 | build成果物配信、manifest CSS/preload。compiler・Node・HMRは外部 |
| 静的生成 / MPA | 連携（0.6） | 外部生成HTMLのdirectory index配信。生成器自体は内蔵しない |
| Static / HTTP cache / download | 内蔵 | multiple roots、ETag、日時条件、Range、Rust streaming。[frontend guide](frontend.md) |
| Compression | 内蔵＋外部 | Rust gzip。Brotli選択・CDN invalidationはproxy/CDN |
| WebSocket / SSE / stream | 内蔵 | native endpoints、bounded queue、Origin、sync/async stream。[機能ガイド](features.md) |
| Session / login / roles / permission | 内蔵 | signed Cookie、fresh login、password hashing。[認証](security-auth.md) |
| OAuth | 内蔵＋外部service | Google/LINE/Discord helper。provider credentials・本番E2Eは利用先で確認 |
| CORS / CSRF / Host / proxy / rate limit | 内蔵 | exact trust境界。rate limitはprocess内。分散quotaは外部 |
| SQLite ORM / transactions | 内蔵 | field/query/commit/rollback。migration、relationships、pool、他DBは専門library |
| Background task | 内蔵 | response後の軽量処理。永続queue・retry・schedulerは外部 |
| Lifespan / supervisor | 内蔵 | sync/async lifecycle、複数process。[デプロイ](deployment.md) |
| Flask compatibility | 部分互換＋WSGI連携 | g/session/flash、request context・Blueprint scoped hooks（0.7）。[対応境界](flask-compat.md)。全extension互換ではない |
| 外部サーバーでHigumaを実行 | 内蔵（0.8） | `asgi_app`: HTTP/WS/lifespan、`wsgi_app`: HTTP。[対応境界](server-interfaces.md)。asyncでも同期互換workerを使用 |
| 他WSGI / ASGI appをmount | HTTP連携 | buffered mount。mounted WS、ASGI lifespan転送、無期限streamは非対応 |
| GraphQL / RPC | 外部library連携 | HTTP互換範囲のWSGI/ASGI appをmount。subscription転送は保証しない |
| App cache / Redis / durable jobs | 外部で構成 | lifespanとDIでclientを管理。HTTP cacheとは別の責任 |
| i18n / email / object storage / payments | 外部で構成 | Python libraryやserviceをview/DIから利用。機密値はserver側だけ |
| PWA / offline / service worker | frontend側 | public file配信可。worker生成・更新・offline戦略は別途必要 |
| Logs / metrics / tracing | logging＋middleware連携 | request hook、Server-Timing等。監視backend・exporterは外部 |
| TLS / HTTP2/3 / CDN / edge WAF | 外部で構成 | trusted reverse proxyで終端し、HigumaはHTTP coreとして動かす |
| Test | 内蔵＋repository suite | test client、request context（0.7）、実Flask比較、実socket HTTP/WS、配布wheel smoke。[テスト](testing.md)。実browserは別途検証 |

## 機能を追加する判断基準

Python実行時依存0・Rustからのstream・明示的なsecurity境界を維持します。
durable queueやdatabase migrationのように運用基盤に依存する機能を、同名の薄いstubで
「対応済み」にしません。外部libraryを組み込む場合も、buffered HTTP・event loop・
process-local state等の制約を事前に確認してください。

組込みではない機能も利用できないという意味ではありません。Higumaが提供するrouting、
middleware、lifespan、DI、mountのどの境界で接続するかを明示して構成します。
