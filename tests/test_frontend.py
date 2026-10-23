import json

import pytest
from higuma import FileResponse, Higuma, SPAFiles, StaticFiles, ViteManifest


@pytest.fixture
def distribution(tmp_path):
    (tmp_path / "index.html").write_text("<html>CSR shell</html>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/main-abcd.js").write_text("console.log('ready')", encoding="utf-8")
    (tmp_path / "assets/style.css").write_text("body { color: black }", encoding="utf-8")
    (tmp_path / "assets/opaque").write_bytes(b"asset")
    (tmp_path / "sw.js").write_text("// service worker", encoding="utf-8")
    (tmp_path / ".hidden").write_text("not public", encoding="utf-8")
    return tmp_path


def make_app(directory, prefix="/"):
    app = Higuma(__name__, static_folder=None)

    @app.get("/api/hello")
    def hello():
        return {"message": "hello"}

    @app.post("/submit")
    def submit():
        return {"ok": True}

    app.mount_spa(prefix, directory)
    return app


@pytest.mark.parametrize("prefix", ["/", "/ui"])
def test_spa_navigation_and_subpath(distribution, prefix):
    app = make_app(distribution, prefix)
    client = app.test_client()
    root = prefix.rstrip("/")
    for suffix in ("/", "/users/42", "/users/%E6%97%A5%E6%9C%AC"):
        response = client.get(root + suffix, headers={"accept": "text/html"})
        assert response.status_code == 200
        assert response.text == "<html>CSR shell</html>"
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["vary"] == "Accept"
        assert response.headers["x-content-type-options"] == "nosniff"
    response = client.head(root + "/users/42")
    assert response.status_code == 200 and response.body == b""
    assert client.get("/api/hello").json == {"message": "hello"}
    assert app.url_for("spa_root") == (prefix if prefix != "/" else "/")
    assert not any(path.startswith("/ui") for path in app.openapi()["paths"])


@pytest.mark.parametrize(
    "path",
    [
        "/api/missing",
        "/assets/missing",
        "/missing.js",
        "/missing.css",
        "/source/file.map",
        "/favicon.ico",
        "/.hidden",
        "/.vite/manifest.json",
        "/%2e%2e/secret",
        "/%2ehidden",
        "/C%3A/secret",
        "/%5Csecret",
        "/a/%00file",
    ],
)
def test_spa_never_uses_html_for_reserved_assets_or_unsafe_paths(distribution, path):
    client = make_app(distribution).test_client()
    response = client.get(path, headers={"accept": "text/html"})
    assert response.status_code == 404
    assert "CSR shell" not in response.text


@pytest.mark.parametrize(
    "accept,status",
    [
        ("application/json", 404),
        ("text/html", 200),
        ("text/*", 200),
        ("*/*", 200),
        ("text/html;q=0, */*;q=1", 404),
        ("text/html;q=invalid", 404),
        ("text/html;q=nan", 404),
        ("text/html;q=2", 404),
    ],
)
def test_spa_accept_negotiation(distribution, accept, status):
    response = make_app(distribution).test_client().get("/screen", headers={"accept": accept})
    assert response.status_code == status


def test_spa_preserves_known_method_contracts_and_api_errors(distribution):
    app = make_app(distribution)
    app.mount_static("/other-assets", distribution, name="other")

    @app.get("/api/error")
    def error():
        return {"error": "not found"}, 404

    client = app.test_client()
    assert client.get("/submit").status_code == 405
    assert client.get("/submit").headers["allow"] == "OPTIONS, POST"
    assert client.options("/submit").headers["allow"] == "OPTIONS, POST"
    assert client.post("/submit").json == {"ok": True}
    assert client.post("/screen").status_code == 405
    assert client.get("/api/error").json == {"error": "not found"}
    assert client.get("/apiary/screen").status_code == 200


def test_static_html_indexes_mounts_and_asset_cache(distribution):
    app = Higuma(__name__, static_folder=None)
    app.mount_static("/files", distribution, html=True, immutable=True, max_age=31536000)
    client = app.test_client()
    redirect = client.get("/files?lang=ja")
    assert redirect.status_code == 308 and redirect.headers["location"] == "/files/?lang=ja"
    assert client.get("/files/").status_code == 200
    script = client.get("/files/assets/main-abcd.js")
    assert script.status_code == 200
    assert script.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert script.media_type.startswith("text/javascript")
    assert client.get("/files/sw.js").headers["cache-control"] == "no-cache"
    assert client.get("/files/index.html").headers["cache-control"] == "no-cache"
    assert client.get("/files/assets/").status_code == 404
    assert client.get("/files/.hidden").status_code == 404


def test_nested_static_html_directory_index(distribution):
    (distribution / "guide").mkdir()
    (distribution / "guide/index.html").write_text("<html>guide</html>", encoding="utf-8")
    app = Higuma(__name__, static_folder=None)
    app.mount_static("/files", distribution, html=True)
    client = app.test_client()
    redirect = client.get("/files/guide?lang=ja")
    assert redirect.status_code == 308
    assert redirect.headers["location"] == "/files/guide/?lang=ja"
    assert client.get("/files/guide/").text == "<html>guide</html>"


def test_file_etag_can_contain_commas_without_matching_a_fragment(distribution):
    app = Higuma(__name__, static_folder=None)

    @app.get("/download")
    def download():
        return FileResponse(distribution / "index.html", headers={"etag": '"part,revision"'})

    client = app.test_client()
    assert client.get("/download", headers={"if-none-match": '"part,revision"'}).status_code == 304
    assert (
        client.get("/download", headers={"if-match": '"other", "part,revision"'}).status_code == 200
    )
    assert (
        client.get("/download", headers={"if-none-match": 'W/"part,revision"'}).status_code == 304
    )
    assert client.get("/download", headers={"if-match": 'W/"part,revision"'}).status_code == 412
    assert client.get("/download", headers={"if-none-match": '"revision"'}).status_code == 200


def test_etag_list_does_not_treat_a_quoted_comma_as_a_separator(distribution):
    app = Higuma(__name__, static_folder=None)

    @app.get("/download")
    def download():
        return FileResponse(distribution / "index.html", headers={"etag": '"revision"'})

    client = app.test_client()
    assert client.get("/download", headers={"if-none-match": '"part,"revision"'}).status_code == 200
    assert client.get("/download", headers={"if-match": '"part,"revision"'}).status_code == 412


@pytest.mark.parametrize(
    "etag,condition,status",
    [
        ('""', 'W/""', 304),
        ('","', '"other", W/","', 304),
        ('"revision"', ' ,\t"other", ,"revision", ', 304),
        ('"revision"', '"revision", *', 200),
        ('"revision"', '"revision" garbage', 200),
        ('"revision"', 'w/"revision"', 200),
        ('"revision"', '"revision", "unterminated', 200),
    ],
)
def test_file_etag_list_grammar(distribution, etag, condition, status):
    app = Higuma(__name__, static_folder=None)

    @app.get("/download")
    def download():
        return FileResponse(distribution / "index.html", headers={"etag": etag})

    assert (
        app.test_client().get("/download", headers={"if-none-match": condition}).status_code
        == status
    )


def test_invalid_mounts_are_atomic_and_configuration_is_checked(distribution):
    app = make_app(distribution)
    original = len(app._routes)
    with pytest.raises(ValueError):
        app.mount_static("/", distribution, name="collision")
    assert len(app._routes) == original
    with pytest.raises(ValueError):
        app.mount_spa("/another", distribution)
    assert len(app._routes) == original
    for prefix in ("relative", "/<name>", "/../oops", "/encoded%20path", "//evil"):
        with pytest.raises(ValueError):
            app.mount_static(prefix, distribution, name="invalid")
    with pytest.raises(ValueError):
        StaticFiles(distribution, max_age=-1)
    with pytest.raises(ValueError):
        StaticFiles(distribution, index_file="../secret")
    with pytest.raises(ValueError):
        SPAFiles(distribution / "missing")


def test_symlinks_cannot_escape_root_or_alias_hidden_files(distribution, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "secret.txt"
    outside.write_text("private", encoding="utf-8")
    try:
        (distribution / "leak.txt").symlink_to(outside)
        (distribution / "hidden-alias").symlink_to(distribution / ".hidden")
    except OSError:
        pytest.skip("symlink creation is not permitted on this platform")
    client = make_app(distribution).test_client()
    assert client.get("/leak.txt").status_code == 404
    assert client.get("/hidden-alias").status_code == 404


@pytest.fixture
def manifest_file(tmp_path):
    manifest = {
        "src/main.js": {
            "file": "assets/main-HASH.js",
            "css": ["assets/main.css"],
            "imports": ["_shared.js", "_shared.js"],
            "dynamicImports": ["lazy.js"],
        },
        "_shared.js": {
            "file": "assets/shared-HASH.js",
            "css": ["assets/shared.css"],
            "imports": ["src/main.js"],
        },
        "lazy.js": {"file": "assets/lazy.js", "css": ["assets/lazy.css"]},
        "styles.css": {"file": "assets/styles.css"},
        "logo.svg": {"file": "assets/logo.svg"},
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_vite_manifest_css_order_preloads_cycles_and_dynamic_imports(manifest_file):
    manifest = ViteManifest(manifest_file, base_url="/ui/")
    assert manifest.entrypoint("src/main.js") == {
        "file": "/ui/assets/main-HASH.js",
        "css": ["/ui/assets/main.css", "/ui/assets/shared.css"],
        "imports": ["/ui/assets/shared-HASH.js"],
    }
    tags = manifest.tags("src/main.js", nonce='safe" onclick="evil')
    assert tags.count('rel="stylesheet"') == 2
    assert tags.count('rel="modulepreload"') == 1
    assert "lazy.js" not in tags
    assert 'nonce="safe&quot; onclick=&quot;evil"' in tags
    assert tags.index("main.css") < tags.index("shared.css") < tags.index("<script")
    assert manifest.tags("styles.css") == '<link rel="stylesheet" href="/ui/assets/styles.css">'
    assert manifest.asset_url("logo.svg") == "/ui/assets/logo.svg"
    with pytest.raises(ValueError):
        manifest.tags("logo.svg")


@pytest.mark.parametrize(
    "base",
    [
        "javascript:alert(1)",
        "//evil.test/",
        "relative",
        "/../assets/",
        "https://user:password@example.test/",
        "/path?query=x",
        "/%2e/",
    ],
)
def test_vite_rejects_unsafe_base_urls(manifest_file, base):
    with pytest.raises(ValueError):
        ViteManifest(manifest_file, base_url=base)


@pytest.mark.parametrize(
    "filename",
    [
        "../secret.js",
        "/secret.js",
        "//evil/js",
        ".hidden.js",
        "assets\\evil.js",
        "https://evil.test/x.js",
        "x%2ejs",
    ],
)
def test_vite_rejects_unsafe_asset_paths(manifest_file, filename):
    manifest_file.write_text(json.dumps({"entry": {"file": filename}}), encoding="utf-8")
    with pytest.raises(ValueError):
        ViteManifest(manifest_file)


def test_vite_reload_is_explicit_and_failure_keeps_previous_snapshot(manifest_file):
    manifest = ViteManifest(manifest_file, base_url="https://cdn.example.test/app/")
    original = manifest.asset_url("src/main.js")
    manifest_file.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        manifest.reload()
    assert manifest.asset_url("src/main.js") == original
    manifest_file.write_text(
        json.dumps({"entry": {"file": "assets/new.js", "imports": ["missing"]}}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="missing imported"):
        manifest.reload()
    assert manifest.asset_url("src/main.js") == original


def test_vite_example_renders_real_template_and_serves_only_public_assets(tmp_path, monkeypatch):
    from examples.vite_ssr import create_app

    (tmp_path / ".vite").mkdir()
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/app-HASH.js").write_text("console.log('ready')", encoding="utf-8")
    (tmp_path / "assets/style.css").write_text("body{}", encoding="utf-8")
    (tmp_path / ".vite/manifest.json").write_text(
        json.dumps(
            {
                "index.html": {"file": "assets/app-HASH.js", "css": ["assets/style.css"]},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HIGUMA_FRONTEND_DIST", str(tmp_path))
    client = create_app().test_client()
    page = client.get("/")
    assert page.status_code == 200
    assert '<script type="module" src="/build/assets/app-HASH.js"></script>' in page.text
    assert '<link rel="stylesheet" href="/build/assets/style.css">' in page.text
    assert client.get("/build/assets/app-HASH.js").status_code == 200
    assert client.get("/build/.vite/manifest.json").status_code == 404


def test_spa_reserved_api_is_never_served_as_a_file(distribution):
    (distribution / "api").mkdir()
    (distribution / "api/config.json").write_text('{"not": "public"}', encoding="utf-8")
    assert make_app(distribution).test_client().get("/api/config.json").status_code == 404


def test_custom_extensionless_html_index_has_html_mime(distribution):
    (distribution / "shell").write_text("<html>custom</html>", encoding="utf-8")
    app = Higuma(__name__, static_folder=None)
    app.mount_spa("/app", distribution, index_file="shell")
    app.mount_static("/help", distribution, name="help", html=True, index_file="shell")
    client = app.test_client()
    for path in ("/app/screen", "/app/shell", "/help/", "/help/shell"):
        response = client.get(path)
        assert response.status_code == 200 and response.text == "<html>custom</html>"
        assert response.media_type == "text/html; charset=utf-8"
        assert response.headers["cache-control"] == "no-cache"


def test_file_validators_and_if_range_share_one_boundary(distribution):
    client = make_app(distribution).test_client()
    first = client.get("/assets/main-abcd.js")
    etag = first.headers["etag"]
    modified = first.headers["last-modified"]
    path = "/assets/main-abcd.js"
    assert client.get(path, headers={"if-none-match": f'"other", W/{etag}'}).status_code == 304
    assert client.head(path, headers={"if-none-match": "*"}).status_code == 304
    assert client.get(path, headers={"if-modified-since": modified}).status_code == 304
    assert client.get(path, headers={"if-modified-since": "invalid"}).status_code == 200
    assert (
        client.get(
            path, headers={"if-none-match": '"stale"', "if-modified-since": modified}
        ).status_code
        == 200
    )
    for validator in (etag, modified):
        part = client.get(path, headers={"range": "bytes=0-6", "if-range": validator})
        assert part.status_code == 206 and part.body == b"console"
    for validator in (
        '"stale"',
        f"W/{etag}",
        "invalid",
        "Wed, 01 Jan 2020 00:00:00 GMT",
        "Fri, 01 Jan 2100 00:00:00 GMT",
    ):
        response = client.get(path, headers={"range": "bytes=0-6", "if-range": validator})
        assert response.status_code == 200 and response.body == first.body
    assert client.get(path, headers={"if-match": '"stale"'}).status_code == 412
    assert client.get(path, headers={"if-match": etag}).status_code == 200
    assert (
        client.get(
            path, headers={"if-unmodified-since": "Wed, 01 Jan 2020 00:00:00 GMT"}
        ).status_code
        == 412
    )
    assert (
        client.get(
            path, headers={"if-match": etag, "if-unmodified-since": "Wed, 01 Jan 2020 00:00:00 GMT"}
        ).status_code
        == 200
    )
