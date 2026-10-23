from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


def on_page_context(
    context: dict[str, Any], *, page: Any, config: Mapping[str, Any], **kwargs: Any
) -> dict[str, Any]:
    source = page.file.src_uri.removeprefix("en/")
    relative_url = page.url.removeprefix("en/")
    alternates = []
    for alternate in config["extra"]["alternate"]:
        root = "en" if alternate["lang"] == "en" else ""
        translated = Path(config["docs_dir"]) / root / source
        link = alternate["link"]
        if translated.is_file():
            link = link.rstrip("/") + "/" + relative_url
        alternates.append({**alternate, "link": link})
    context["higuma_alternates"] = alternates
    return context


def on_post_build(config: Mapping[str, Any]) -> None:
    # Material's language selector looks for a sitemap under each language root.
    site = Path(config["site_dir"])
    root = ElementTree.parse(site / "sitemap.xml").getroot()
    english = ElementTree.Element(root.tag, root.attrib)
    prefix = str(config["site_url"]).rstrip("/") + "/en/"
    for entry in root:
        location = entry.find("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
        if location is not None and (location.text or "").startswith(prefix):
            english.append(entry)
    destination = site / "en" / "sitemap.xml"
    destination.parent.mkdir(parents=True, exist_ok=True)
    ElementTree.register_namespace("", "http://www.sitemaps.org/schemas/sitemap/0.9")
    ElementTree.ElementTree(english).write(destination, encoding="utf-8", xml_declaration=True)
