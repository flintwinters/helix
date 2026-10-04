"""Check the static project page's navigation, assets, and search metadata."""

import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = set()
        self.links = []
        self.metadata = {}
        self.headings = 0
        self.structured_data = ""
        self.in_schema = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            assert attrs["id"] not in self.ids, f"Duplicate ID: {attrs['id']}"
            self.ids.add(attrs["id"])
        if tag in {"a", "link"}:
            self.links.append(attrs.get("href", ""))
        if tag == "meta":
            self.metadata[attrs.get("name", attrs.get("property"))] = attrs.get("content")
        if tag == "link" and attrs.get("rel") == "canonical":
            self.metadata["canonical"] = attrs["href"]
        if tag == "h1":
            self.headings += 1
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self.in_schema = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_schema = False

    def handle_data(self, data):
        if self.in_schema:
            self.structured_data += data


def check_site():
    root = Path(__file__).resolve().parents[1] / "site"
    page = Page()
    page.feed((root / "index.html").read_text())
    assert page.headings == 1, "Use one primary page heading."
    for link in page.links:
        url = urlsplit(link)
        if not url.scheme and not url.netloc:
            if url.path:
                assert (root / url.path).is_file(), f"Missing local asset: {link}"
            if url.fragment:
                assert url.fragment in page.ids, f"Missing section: {link}"
    for key in ("description", "og:title", "og:description", "og:url", "canonical"):
        assert page.metadata.get(key), f"Missing search metadata: {key}"
    canonical = page.metadata["canonical"]
    assert page.metadata["og:url"] == canonical, "Social and canonical URLs disagree."
    assert json.loads(page.structured_data)["url"] == canonical, "Schema URL disagrees."
    sitemap = ElementTree.parse(root / "sitemap.xml")
    assert sitemap.find(".//{*}loc").text == canonical, "Sitemap URL disagrees."
    assert f"Sitemap: {canonical}sitemap.xml" in (root / "robots.txt").read_text()
    print("Project page: assets, section links, and search metadata pass.")
