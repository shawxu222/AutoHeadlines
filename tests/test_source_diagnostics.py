from __future__ import annotations

from dataclasses import dataclass

from src.collectors import source_diagnostics
from src.parsers.article_extractor import ExtractedArticle


@dataclass
class FakeResponse:
    text: str
    url: str = "https://example.com/science"
    content: bytes = b""
    headers: dict[str, str] | None = None

    def __post_init__(self) -> None:
        self.headers = self.headers or {"content-type": "text/html"}

    def raise_for_status(self) -> None:
        return None


class FakeExtractor:
    def __init__(self, timeout_seconds=20):  # noqa: ANN001
        self.timeout_seconds = timeout_seconds

    def extract_from_url(self, url):  # noqa: ANN001
        return ExtractedArticle(
            title="A useful science article",
            text="research " * 80,
            html="<article>research</article>",
            published_date="2026-06-12",
        )


def test_diagnose_public_source_can_recommend_and_pass(monkeypatch) -> None:
    html = """
    <html><head>
      <link rel="alternate" type="application/rss+xml" href="/science/rss.xml">
    </head><body><main>
      <a href="/science/2026/new-chip">New AI chip research reaches production</a>
      <a href="/science/2026/quantum">Quantum research milestone announced</a>
      <a href="/science/2026/robotics">Robotics research project launches</a>
    </main></body></html>
    """
    monkeypatch.setattr(
        source_diagnostics.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(html),
    )
    monkeypatch.setattr(source_diagnostics, "ArticleExtractor", FakeExtractor)

    report = source_diagnostics.diagnose_source(
        {
            "name": "Example Science",
            "base_url": "https://example.com",
            "section_url": "https://example.com/science",
            "source_type": "official",
            "include_url_patterns": [r"/science/2026/"],
        }
    )

    assert report["status"] == "ready"
    assert report["candidate_count"] == 3
    assert report["detected_feeds"] == ["https://example.com/science/rss.xml"]
    assert report["suggested_patch"]["include_url_patterns"] == [
        r"^https?://example\.com/science/"
    ]


def test_diagnose_login_source_requires_adapter() -> None:
    report = source_diagnostics.diagnose_source(
        {
            "name": "Members Only",
            "base_url": "https://example.com",
            "source_type": "login_browser",
            "requires_login": True,
        }
    )

    assert report["status"] == "needs_adapter"


def test_private_urls_are_rejected() -> None:
    assert source_diagnostics.public_url_validation_error("http://127.0.0.1/private")
    assert source_diagnostics.public_url_validation_error("http://192.168.1.10/private")
    assert not source_diagnostics.public_url_validation_error("https://example.com/news")
