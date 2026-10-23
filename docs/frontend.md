# CSR・SPA・フロントエンド連携

このページのAPIには0.6.0以降が必要です。0.5.0には`mount_spa`・`mount_static`・
`ViteManifest`はありません。`python -m pip install -U higuma`で更新してください。
サンプルの実行・開発にはrepositoryを取得し、rootでeditable installします。

## どの方式を使うか

| 方式 | HTMLと画面の担当 | Higumaの使い方 |
| --- | --- | --- |
| SSR | Python viewとMiniJinjaでHTML生成 | `app.render_template()` |
| CSR / SPA | 最初のHTMLを配信後、browser上でJSが画面生成・遷移 | `app.mount_spa()`＋API |
| SSR HTML＋Vite assets | PythonでHTML生成し、bundleをbrowserで実行 | `ViteManifest`＋`mount_static()` |
| 静的生成 / MPA | 外部builderで複数HTMLを生成 | `mount_static(..., html=True)` |

React・Vue・Svelte等のコンパイラ、Node runtime、React component SSRは内蔵しません。
それぞれのbuilderが出力したHTML/JS/CSSを配信します。認証・DB・APIはHiguma側で実装します。

## 追加依存なしでCSRを試す

```bash
python -m pip install -e ".[dev]"
higuma run examples.csr_app:create_app --port 8000
```

`http://127.0.0.1:8000/app/`を開きます。「APIステータス」を押すと`GET /api/status`の
実JSON応答が表示されます。`/app/status`を直接開く・再読み込みする・戻る操作にも対応します。
`examples/frontend/`は標準ES module・History API・fetchのみで動くサンプルです。

## ビルド済みSPAを配信する

```python
from higuma import Higuma

app = Higuma(__name__, static_folder=None)

@app.get("/api/hello")
def hello():
    return {"message": "hello"}

app.mount_spa("/", "frontend/dist")
```

指定directoryに`index.html`が必要です。directory/indexの欠落は登録時に`ValueError`となり、
空の配信設定で起動することを防ぎます。相対directoryは**プロセスの作業directory基準**です。
application module基準にしたい場合は`Path(__file__).parent / "frontend/dist"`等を渡します。

サブパスでは次のようにします。

```python
app.mount_spa("/app", "frontend/dist", name="client")
```

frontendのasset baseとrouter basenameも`/app/`へ揃えます。Viteなら`base: '/app/'`を設定して
再ビルドします。React RouterのbasenameやVue Routerのhistory baseもfrontend側の責任です。
絶対パス`/assets/...`を生成したbundleを、そのまま`/app/`配下へ移してもURLは変わりません。

### history fallbackの条件

- GET/HEADだけを受け付け、通常の明示routeを優先します。既知のPOST専用routeへのGETは405です。
- 実在する公開fileはそのまま配信します。directory listingは行いません。
- 未存在の**拡張子なしURL**はHTML shellへfallbackします。dotを含むpath segmentはfallbackしません。
- `api_prefixes=("/api",)`は予約領域です。`asset_prefixes=("/assets",)`では実在fileのみ配信し、
  拡張子のない欠損assetもHTMLにしません。どちらもmount内の相対prefixで、segment境界で照合します。
- AcceptがHTMLを拒否するrequest（例:`application/json`、`text/html;q=0`）はfallbackしません。
  Accept未指定・`*/*`は許可します。APIは必ず予約prefixまたは明示routeへ分けてください。
- アプリが返した404/401/403/500を後からHTMLに置き換える処理ではありません。
- dotfile、dot segment、backslash、drive/ADS構文、root外へのsymlinkは配信しません。

```python
app.mount_spa(
    "/app", "frontend/dist", name="client",
    api_prefixes=("/api", "/graphql"),
    asset_prefixes=("/assets", "/fonts", "/images"),
)
```

同じGET pathやendpoint名が衝突すると登録前に`ValueError`になります。
複数mountでは`name`を変えます。`url_for("client_root")`と
`url_for("client_path", filename="assets/main.js")`で配信URLを生成できます。

## 複数static rootと静的HTML

```python
app.mount_static("/media", "public/media", name="media", max_age=3600)
app.mount_static("/help", "public/help", name="help", html=True)
app.mount_static("/assets", "frontend/dist/assets", name="bundles",
                 max_age=31536000, immutable=True)
```

`html=True`ではdirectory内の`index.html`を配信し、末尾slashのないdirectory URLは308で
slash付きへredirectします。query stringは維持します。`html=False`ではdirectoryは404です。
`index_file`で別の可視filenameを指定できます。隠しfileやroot外へのsymlinkは常に拒否します。

`immutable=True`はURLが内容に応じて変わるhash付きasset専用にしてください。同じURLを上書きする
画像等に長期immutableを付けると、更新が利用者へ届きません。HTML・`sw.js`・`service-worker.js`の
既定cacheは`no-cache`です。SPA shellは常に`no-cache`＋`Vary: Accept`になります。
その他のfileは既定`public, max-age=3600`、`X-Content-Type-Options: nosniff`を付けます。

file bodyはRustからstreamされます。全fileの`FileResponse`と`send_file()`も含め、
GET/HEADで次の条件付き配信を共通実装します。

| Request header | 動作 |
| --- | --- |
| `If-None-Match` | ETag list・weak comparison・`*`に対応し、未変更なら304 |
| `If-Modified-Since` | 未更新なら304。`If-None-Match`があればそちらを優先 |
| `If-Match` | strong comparison。不一致なら412 |
| `If-Unmodified-Since` | 更新済みなら412。`If-Match`があればそちらを優先 |
| `Range: bytes=...` | single rangeは206、不成立・複数rangeは416 |
| `If-Range` | 現在のstrong ETagまたはLast-Modified日時と一致した場合だけ206。不一致・weak値なら全体200 |

`Last-Modified`とETagはfile metadataから生成します。ETagは暗号学的な内容署名ではありません。
書込みAPIの同時更新制御はこの配信処理とは別に実装します。

## Vite：開発時と本番

### 開発時

Vite dev serverでfrontendを開き、APIをHigumaへproxyする構成が簡単です。
既存Vite projectの設定へ統合してください。この設定自体はHigumaのPython依存を増やしません。

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

HMRはViteの機能です。HigumaがNodeを起動したり、ViteのWebSocketを代理したりする機能ではありません。
開発時はfrontendのlocal URLのroot、本番はHigumaの`/app/`へアクセスします。
router baseは`import.meta.env.BASE_URL`を使う等で両方に合わせます。この例は開発時のbaseをrootにして
`/api` proxyと揃えています。非root baseのまま開発する場合は[Viteのproxy規則](https://vite.dev/config/server-options#server-proxy)
に従い、proxy keyとfrontendのAPI URLにもbaseを反映してください。dev serverを無制限の
CORSで本番公開しないでください。通常のReact/Vue SPAはbuildしたindex.htmlをそのままmountでき、
次のmanifest helperは必須ではありません。

### PythonでHTMLを生成する場合

Viteで`build.manifest: true`を指定すると`.vite/manifest.json`が生成されます。
Python側でそのsnapshotを読み、entryと依存CSS/JSのURLを解決できます。

```python
from higuma import ViteManifest

vite = ViteManifest("frontend/dist/.vite/manifest.json", base_url="/build/")
logo_url = vite.asset_url("src/logo.svg")  # manifestに存在するkeyを指定
tags = vite.tags("index.html", nonce="request-specific-csp-nonce")
```

`base_url`とViteの`base`を`/build/`へ揃え、`app.mount_static("/build", "frontend/dist")`で配信します。
`examples/vite_ssr.py`は`HIGUMA_FRONTEND_DIST`（既定`frontend/dist`）から自分のVite buildを読みます。
SSRするのはPython側のHTMLであり、React componentをserverで実行する例ではありません。

```python
return app.render_template("vite.html", vite_tags=vite.tags("index.html"))
```

```html
<!-- vite_tagsはこのhelperで生成した文字列だけを渡す -->
{{ vite_tags | safe }}
```

- `asset_url(entry)`は単一の出力URLを返します。未知keyは`KeyError`です。
- `entrypoint(entry)`は`file`、`css`、`imports`のURLを返します。
- `tags(entry, nonce=None)`はentry CSS→依存CSS→module script→modulepreloadの順でHTMLを生成します。
  同じCSS/importは重複せず、循環importで無限反復しません。CSS-only entryも扱います。
- dynamic importは先読みせず、bundleの実行時importに任せます。画像等は`asset_url()`で扱います。
- URLとnonceはHTML escapeします。相対file以外のmanifest出力や危険なbase URLは拒否します。
- 読込は起動時／明示`reload()`時のみです。不正snapshotへのreload失敗時は以前のsnapshotを維持します。
  buildの入替えはatomicに行い、古いhash assetも既存タブの利用期間は保持してください。
- Node rendering、React Refresh preamble、experimental chunk import mapの自動挿入は提供しません。

## Cookie認証・CORS・本番運用

CSRでも[sessionとCSRF](security-auth.md)の責任は変わりません。Cookie認証で更新APIを呼ぶ場合は、
最初のGETでCSRF tokenを取得し、POST/PATCH/DELETEへ`X-CSRF-Token`を送ります。
token・OAuth client secret・DB credentialsをJS bundleやpublic directoryへ入れないでください。

別originから呼ぶ場合は`CORSMiddleware`で許可originを明示し、必要な場合だけcredentialsを許可します。
browserのfetchには`credentials: 'include'`も必要です。CORSは認証・CSRF防御の代替ではありません。
`SameSite`・Secure Cookie・HTTPS・trusted Host/proxy・WebSocket Originも本番に合わせます。

- ユーザー固有のAPI応答・tokenには`Cache-Control: no-store`を使い、public cacheへ混ぜない。
- public directoryはビルド出力だけにし、source、秘密情報、DB、server側upload保存先を混ぜない。
- SPA shellはルーター上の未知画面でもHTTP200です。SEOや厳密なHTTP404が必要な画面にはSSR/静的生成を使う。
- staleタブのlazy chunk欠落を避けるため、更新はatomicにし、古いassetsを一定期間維持する。
- service workerの生成・offline cache更新戦略・web manifestはfrontendで管理する。Higumaが自動PWA化するわけではない。
- precompressed Brotli asset選択、CDN purge、分散cache・job queueは外部proxy/serviceで構成する。

公式仕様の参照先: [Vite backend integration](https://vite.dev/guide/backend-integration)、
[React Router SPA](https://reactrouter.com/how-to/spa)。全体の対応範囲は[機能対応表](capabilities.md)を参照してください。
