from __future__ import annotations

import os
import time
from email.utils import parsedate_to_datetime

import feedparser

from src.config_loader import NewsSource
from src.fetchers.base import Article
from src.parsers.article_extractor import ArticleExtractor
from src.parsers.text_cleaner import clean_text
from src.utils.logger import get_logger


logger = get_logger(__name__)


class RSSFetcher:
    def __init__(self, extractor: ArticleExtractor | None = None) -> None:
        self.extractor = extractor or ArticleExtractor()
        self.limit = int(os.getenv("FETCH_LIMIT_PER_SOURCE", "20"))
        self.delay = float(os.getenv("REQUEST_DELAY_SECONDS", "1.0"))

    def fetch(self, source: NewsSource) -> list[Article]:
        if source.requires_login:
            logger.info("Skipping login-required source: %s", source.name)
            return []

        parsed = feedparser.parse(source.section_url)
        articles: list[Article] = []
        for entry in parsed.entries[: self.limit]:
            try:
                url = str(entry.get("link", "")).strip()
                if not url:
                    continue
                title = clean_text(str(entry.get("title", "")))
                published_date = self._entry_date(entry)
                fallback_text = clean_text(
                    str(entry.get("summary", "") or entry.get("description", ""))
                )

                try:
                    extracted = self.extractor.extract_from_url(url)
                    raw_text = extracted.text or fallback_text
                    title = title or extracted.title
                except Exception as exc:
                    logger.warning("Article extraction failed for %s: %s", url, exc)
                    raw_text = fallback_text

                if not title and not raw_text:
                    continue

                articles.append(
                    Article(
                        title_original=title,
                        source=source.name,
                        country_region=source.country_region,
                        language=source.language,
                        published_date=published_date,
                        url=url,
                        raw_text=raw_text,
                        source_priority=source.priority,
                        source_type=source.source_type,
                        tags=source.tags,
                    )
                )
                time.sleep(self.delay)
            except Exception as exc:
                logger.exception("RSS entry failed for %s: %s", source.name, exc)
        return articles

    @staticmethod
    def _entry_date(entry) -> str:
        for key in ["published", "updated", "created"]:
            value = entry.get(key)
            if not value:
                continue
            try:
                return parsedate_to_datetime(str(value)).date().isoformat()
            except Exception:
                return str(value)
        return ""
