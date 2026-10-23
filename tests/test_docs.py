from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

from tools.docs_hooks import on_page_context, on_post_build


@pytest.mark.parametrize("language", ["", "en/"])
def test_language_links_stay_on_the_translated_page(tmp_path, language):
    (tmp_path / "en").mkdir()
    (tmp_path / "frontend.md").write_text("# Frontend", encoding="utf-8")
    (tmp_path / "en/frontend.md").write_text("# Frontend", encoding="utf-8")
    config = {
        "docs_dir": str(tmp_path),
        "extra": {
            "alternate": [
                {"name": "日本語", "lang": "ja", "link": "/"},
                {"name": "English", "lang": "en", "link": "/en/"},
            ]
        },
    }
    page = SimpleNamespace(
        file=SimpleNamespace(src_uri=language + "frontend.md"), url=language + "frontend/"
    )
    context = on_page_context({}, page=page, config=config)
    assert [entry["link"] for entry in context["higuma_alternates"]] == [
        "/frontend/",
        "/en/frontend/",
    ]
    assert config["extra"]["alternate"][1]["link"] == "/en/"
    missing = SimpleNamespace(file=SimpleNamespace(src_uri="missing.md"), url="missing/")
    context = on_page_context({}, page=missing, config=config)
    assert [entry["link"] for entry in context["higuma_alternates"]] == ["/", "/en/"]


@pytest.mark.parametrize("base", ["https://docs.example/", "https://docs.example/project/"])
def test_english_sitemap_is_generated_without_changing_global_sitemap(tmp_path, base):
    original = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{base}</loc></url>"
        f"<url><loc>{base}en/</loc></url>"
        f"<url><loc>{base}en/frontend/</loc><lastmod>2026-10-23</lastmod></url>"
        f"<url><loc>{base}enough/</loc></url>"
        "</urlset>"
    )
    (tmp_path / "sitemap.xml").write_text(original, encoding="utf-8")
    on_post_build({"site_dir": str(tmp_path), "site_url": base})
    root = ElementTree.parse(tmp_path / "en/sitemap.xml").getroot()
    namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    assert [node.text for node in root.findall("s:url/s:loc", namespace)] == [
        base + "en/",
        base + "en/frontend/",
    ]
    assert root.find("s:url/s:lastmod", namespace).text == "2026-10-23"
    assert "<urlset xmlns=" in (tmp_path / "en/sitemap.xml").read_text(encoding="utf-8")
    assert (tmp_path / "sitemap.xml").read_text(encoding="utf-8") == original
    on_post_build({"site_dir": str(tmp_path), "site_url": base})
    assert len(ElementTree.parse(tmp_path / "en/sitemap.xml").getroot()) == 2
