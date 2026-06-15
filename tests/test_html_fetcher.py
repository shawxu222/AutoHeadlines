from __future__ import annotations

from bs4 import BeautifulSoup

from src.config_loader import NewsSource
from src.fetchers.html_fetcher import HTMLFetcher


def test_html_fetcher_uses_configured_selectors_and_url_patterns() -> None:
    soup = BeautifulSoup(
        """
        <main>
          <a class="story" href="/story/123">Short</a>
          <a class="story" href="/sports/story/456">Sports story</a>
          <a href="/story/789">Not selected</a>
        </main>
        """,
        "html.parser",
    )
    source = NewsSource(
        name="Example",
        country_region="US",
        language="en",
        section_url="https://example.com/latest",
        source_type="html",
        requires_login=False,
        priority=3,
        link_selectors=["a.story"],
        include_url_patterns=[r"/story/\d+$"],
        exclude_url_patterns=[r"/sports/"],
    )

    assert HTMLFetcher()._discover_article_links(soup, source) == [  # noqa: SLF001
        ("Short", "https://example.com/story/123")
    ]
