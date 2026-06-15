from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.config_loader import DATA_ROOT, PROJECT_ROOT, NewsSource
from src.fetchers.base import Article
from src.parsers.text_cleaner import clean_text
from src.utils.logger import get_logger


logger = get_logger(__name__)


class ManualFetcher:
    """Read manually collected rows from an Excel or CSV file."""

    def fetch(self, source: NewsSource) -> list[Article]:
        path = Path(source.section_url)
        if not path.is_absolute():
            path = (
                DATA_ROOT / Path(*path.parts[1:])
                if path.parts[:1] == ("data",)
                else PROJECT_ROOT / path
            )
        if not path.exists():
            logger.info("Manual import file not found: %s", path)
            return []

        try:
            if path.suffix.lower() in {".xlsx", ".xls"}:
                frame = pd.read_excel(path)
            else:
                frame = pd.read_csv(path)
        except Exception as exc:
            logger.exception("Manual import failed: %s", exc)
            return []

        articles: list[Article] = []
        for _, row in frame.fillna("").iterrows():
            title = clean_text(row.get("title") or row.get("标题") or row.get("title_original"))
            url = clean_text(row.get("url") or row.get("URL") or row.get("链接"))
            raw_text = clean_text(row.get("raw_text") or row.get("正文") or row.get("content"))
            if not title and not raw_text:
                continue
            articles.append(
                Article(
                    title_original=title,
                    source=source.name,
                    country_region=source.country_region,
                    language=source.language,
                    published_date=clean_text(
                        row.get("published_date") or row.get("发布日期") or ""
                    ),
                    url=url,
                    raw_text=raw_text,
                    source_priority=source.priority,
                    source_type=source.source_type,
                    tags=source.tags,
                )
            )
        return articles
