from __future__ import annotations

import os
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from src.collectors.discovery import _is_relevant_for_source, _select_anchors
from src.config_loader import NewsSource
from src.fetchers.base import Article
from src.parsers.article_extractor import ArticleExtractor, DEFAULT_HEADERS
from src.parsers.text_cleaner import clean_text
from src.utils.logger import get_logger


logger = get_logger(__name__)


class HTMLFetcher:
    """Generic HTML listing fetcher for public news pages."""

    def __init__(self, extractor: ArticleExtractor | None = None) -> None:
        self.extractor = extractor or ArticleExtractor()
        self.limit = int(os.getenv("FETCH_LIMIT_PER_SOURCE", "20"))
        self.delay = float(os.getenv("REQUEST_DELAY_SECONDS", "1.0"))
        self.timeout = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20"))

    def fetch(self, source: NewsSource) -> list[Article]:
        if source.requires_login:
            logger.info("Skipping login-required source: %s", source.name)
            return []

        try:
            response = requests.get(
                source.section_url,
                headers=DEFAULT_HEADERS,
                timeout=self.timeout,
                allow_redirects=True,
            )
            response.raise_for_status()
            if not response.encoding:
                response.encoding = response.apparent_encoding
        except Exception as exc:
            logger.exception("HTML source request failed for %s: %s", source.name, exc)
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        links = self._discover_article_links(soup, source)
        articles: list[Article] = []

        for title_hint, url in links[: self.limit]:
            try:
                extracted = self.extractor.extract_from_url(url)
                title = clean_text(title_hint or extracted.title)
                raw_text = extracted.text
                if not title and not raw_text:
                    continue
                articles.append(
                    Article(
                        title_original=title,
                        source=source.name,
                        country_region=source.country_region,
                        language=source.language,
                        published_date="",
                        url=url,
                        raw_text=raw_text,
                        source_priority=source.priority,
                        source_type=source.source_type,
                        tags=source.tags,
                    )
                )
                time.sleep(self.delay)
            except Exception as exc:
                logger.warning("HTML article failed for %s: %s", url, exc)

        return articles

    def _discover_article_links(
        self, soup: BeautifulSoup, source: NewsSource
    ) -> list[tuple[str, str]]:
        seen: set[str] = set()
        links: list[tuple[str, str]] = []
        base_url = source.section_url
        base_domain = urlparse(base_url).netloc

        selectors = source.link_selectors or [
            "article a[href]",
            "main a[href]",
            ".news a[href]",
            ".press a[href]",
            ".list a[href]",
            "a[href]",
        ]
        for anchor in _select_anchors(soup, selectors):
            href = anchor.get("href")
            if not href:
                continue
            url = urljoin(base_url, href)
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"}:
                continue
            if parsed.netloc and base_domain and parsed.netloc != base_domain:
                continue
            title = clean_text(anchor.get_text(" ", strip=True))
            if source.include_url_patterns or source.exclude_url_patterns:
                is_article = _is_relevant_for_source(source, url, title)
            else:
                is_article = self._looks_like_article_url(url, title)
            if not is_article or url in seen:
                continue
            seen.add(url)
            links.append((title, url))
            if len(links) >= self.limit * 2:
                return links
        return links

    @staticmethod
    def _looks_like_article_url(url: str, title: str) -> bool:
        if len(title) >= 12:
            return True
        lowered = url.lower()
        hints = [
            "news",
            "press",
            "release",
            "article",
            "topics",
            "posts",
            "202",
            "research",
        ]
        return any(hint in lowered for hint in hints)
