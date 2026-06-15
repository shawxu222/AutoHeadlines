from __future__ import annotations

import os
import json
import re
from dataclasses import dataclass
from typing import Iterable

import requests
from bs4 import BeautifulSoup

from src.parsers.text_cleaner import clean_text
from src.utils.logger import get_logger


logger = get_logger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja-JP,ja;q=0.9,ko-KR,ko;q=0.8,zh-CN,zh;q=0.7,en;q=0.6",
}


@dataclass(slots=True)
class ExtractedArticle:
    title: str
    text: str
    html: str
    published_date: str = ""


class ArticleExtractor:
    """Generic public-web article extractor for MVP usage."""

    def __init__(self, timeout_seconds: int | None = None) -> None:
        self.timeout_seconds = timeout_seconds or int(
            os.getenv("REQUEST_TIMEOUT_SECONDS", "20")
        )

    def fetch_html(self, url: str) -> str:
        response = requests.get(
            url,
            headers=DEFAULT_HEADERS,
            timeout=self.timeout_seconds,
            allow_redirects=True,
        )
        response.raise_for_status()
        if response.apparent_encoding:
            response.encoding = response.apparent_encoding
        elif not response.encoding:
            response.encoding = response.apparent_encoding
        return response.text

    def extract_from_url(self, url: str) -> ExtractedArticle:
        html = self.fetch_html(url)
        return self.extract_from_html(html)

    def extract_from_html(self, html: str) -> ExtractedArticle:
        soup = BeautifulSoup(html, "html.parser")
        title = self._extract_title(soup)

        for tag in soup(["script", "style", "noscript", "svg", "form"]):
            tag.decompose()
        for selector in ["nav", "header", "footer", "aside"]:
            for tag in soup.select(selector):
                tag.decompose()

        main_node = self._best_content_node(soup)
        text = clean_text(main_node.get_text(" ", strip=True) if main_node else soup.get_text(" "))
        published_date = self._extract_published_date(soup, text)
        return ExtractedArticle(title=title, text=text, html=html, published_date=published_date)

    @staticmethod
    def _extract_title(soup: BeautifulSoup) -> str:
        candidates = []
        if soup.title and soup.title.string:
            candidates.append(soup.title.string)
        for selector in ["h1", "meta[property='og:title']", "meta[name='twitter:title']"]:
            node = soup.select_one(selector)
            if not node:
                continue
            if node.name == "meta":
                candidates.append(node.get("content", ""))
            else:
                candidates.append(node.get_text(" ", strip=True))
        for candidate in candidates:
            cleaned = clean_text(candidate)
            if cleaned:
                return cleaned
        return ""

    @staticmethod
    def _extract_published_date(soup: BeautifulSoup, text: str) -> str:
        selectors = [
            "meta[property='article:published_time']",
            "meta[property='og:published_time']",
            "meta[name='pubdate']",
            "meta[name='publishdate']",
            "meta[name='date']",
            "meta[name='dc.date']",
            "meta[itemprop='datePublished']",
            "time[datetime]",
        ]
        for selector in selectors:
            node = soup.select_one(selector)
            if not node:
                continue
            value = node.get("content") or node.get("datetime") or node.get_text(" ", strip=True)
            normalized = ArticleExtractor._normalize_date_text(value)
            if normalized:
                return normalized

        for script in soup.select("script[type='application/ld+json']"):
            try:
                payload = json.loads(script.string or "{}")
            except Exception:
                continue
            values = payload if isinstance(payload, list) else [payload]
            for item in values:
                if not isinstance(item, dict):
                    continue
                value = item.get("datePublished") or item.get("dateCreated") or item.get("dateModified")
                normalized = ArticleExtractor._normalize_date_text(value)
                if normalized:
                    return normalized

        return ArticleExtractor._normalize_date_text(text[:2000])

    @staticmethod
    def _normalize_date_text(value: object) -> str:
        raw = clean_text(value)
        if not raw:
            return ""
        match = re.search(
            r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})[日 T曜　]*(\d{1,2})?:?(\d{2})?",
            raw,
        )
        if not match:
            return ""
        year, month, day, hour, minute = match.groups()
        if hour and minute:
            return f"{int(year):04d}-{int(month):02d}-{int(day):02d} {int(hour):02d}:{int(minute):02d}"
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    @staticmethod
    def _candidate_nodes(soup: BeautifulSoup) -> Iterable:
        selectors = [
            "article",
            "main",
            "[role='main']",
            ".article",
            ".article-body",
            ".article-content",
            ".entry-content",
            ".post-content",
            ".content",
            "#content",
        ]
        for selector in selectors:
            for node in soup.select(selector):
                yield node

    def _best_content_node(self, soup: BeautifulSoup):
        candidates = list(self._candidate_nodes(soup))
        if not candidates:
            return soup.body or soup
        return max(candidates, key=lambda node: len(node.get_text(" ", strip=True)))
