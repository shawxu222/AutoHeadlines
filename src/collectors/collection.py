from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from src.config_loader import DATA_ROOT
from src.fetchers.base import Article
from src.parsers.article_extractor import ArticleExtractor
from src.parsers.text_cleaner import clean_text
from src.storage.database import save_articles
from src.utils.date_window import parse_article_datetime
from src.utils.dates import compact_date
from src.utils.jsonl import read_jsonl, write_jsonl
from src.utils.logger import get_logger


logger = get_logger(__name__)


def articles_path(run_date: str) -> Path:
    return DATA_ROOT / "processed" / f"articles_{compact_date(run_date)}.jsonl"


def collect_articles(
    run_date: str,
    discovered_file: Path | None = None,
    stop_event: Any | None = None,
    progress_callback: Any | None = None,
) -> list[dict[str, Any]]:
    from src.collectors.discovery import discovered_path

    discovered_file = discovered_file or discovered_path(run_date)
    discovered = read_jsonl(discovered_file)
    extractor = ArticleExtractor()
    articles: list[Article] = []
    rows: list[dict[str, Any]] = []

    for index, item in enumerate(discovered, start=1):
        if stop_event is not None and stop_event.is_set():
            logger.info("Article collection stopped by user request.")
            break
        if progress_callback is not None:
            progress_callback(index, len(discovered), item)
        try:
            url = str(item.get("url", ""))
            if not url:
                continue
            extracted = extractor.extract_from_url(url)
            if _stop_requested(stop_event):
                logger.info("Article collection stopped after current fetch.")
                break
            title = clean_text(extracted.title or item.get("title_original", ""))
            raw_text = clean_text(extracted.text)
            published_date = _best_published_date(
                extracted.published_date,
                item.get("published_date", ""),
                title,
                raw_text,
            )
            warning = _merge_warnings(
                item.get("extraction_warning", ""),
                _extraction_warning(title, raw_text, published_date),
            )
            article = Article(
                title_original=title,
                source=str(item.get("source", "")),
                country_region=str(item.get("country_region", "")),
                language=str(item.get("language", "")),
                published_date=published_date,
                url=url,
                raw_text=raw_text,
                source_priority=int(item.get("source_priority") or 1),
                source_type=str(item.get("source_type", "")),
                tags=[str(tag) for tag in item.get("tags", [])],
                source_domain=str(item.get("source_domain", "")),
                extraction_warning=warning,
            )
            articles.append(article)
            row = _article_to_row(article)
            row["max_articles_per_run"] = item.get("max_articles_per_run", "")
            rows.append(row)
            if not _sleep_with_stop(float(item.get("rate_limit_seconds") or 1.0), stop_event):
                logger.info("Article collection stopped during rate-limit wait.")
                break
        except Exception as exc:
            logger.exception("Article collection failed for %s: %s", item.get("url"), exc)
            rows.append(
                {
                    **item,
                    "raw_text": "",
                    "raw_text_preview": "",
                    "published_date": item.get("published_date", ""),
                    "extraction_warning": _merge_warnings(
                        item.get("extraction_warning", ""), f"collection_failed: {exc}"
                    ),
                }
            )

    save_articles(run_date, articles)
    write_jsonl(articles_path(run_date), rows)
    return rows


def _stop_requested(stop_event: Any | None) -> bool:
    return bool(stop_event is not None and stop_event.is_set())


def _sleep_with_stop(seconds: float, stop_event: Any | None) -> bool:
    if seconds <= 0:
        return not _stop_requested(stop_event)
    if stop_event is None:
        time.sleep(seconds)
        return True
    return not stop_event.wait(seconds)


def _best_published_date(
    extracted_date: Any, discovered_date: Any, title: str, raw_text: str
) -> str:
    for value in [extracted_date, discovered_date]:
        normalized = _normalize_valid_published_date(value)
        if normalized:
            return normalized
    return _fallback_published_date(title, raw_text)


def _normalize_valid_published_date(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    parsed, has_time = parse_article_datetime(text)
    if parsed is None:
        return ""
    if has_time:
        return f"{parsed:%Y-%m-%d %H:%M}"
    return f"{parsed:%Y-%m-%d}"


def _fallback_published_date(title: str, raw_text: str) -> str:
    labeled_date = _labeled_published_date(raw_text)
    if labeled_date:
        return labeled_date
    parsed, has_time = parse_article_datetime(f"{title} {raw_text[:1500]}")
    return _format_published_date(parsed, has_time)


def _labeled_published_date(raw_text: str) -> str:
    for label in ("最終更新日", "掲載日", "公開日", "更新日"):
        match = re.search(rf"{re.escape(label)}\s*[:：]?\s*(.{{0,80}})", raw_text)
        if not match:
            continue
        parsed, has_time = parse_article_datetime(match.group(1))
        formatted = _format_published_date(parsed, has_time)
        if formatted:
            return formatted
    return ""


def _format_published_date(parsed: Any, has_time: bool) -> str:
    if parsed is None:
        return ""
    if has_time:
        return f"{parsed:%Y-%m-%d %H:%M}"
    return f"{parsed:%Y-%m-%d}"


def _article_to_row(article: Article) -> dict[str, Any]:
    return {
        "title_original": article.title_original,
        "source": article.source,
        "country_region": article.country_region,
        "language": article.language,
        "published_date": article.published_date,
        "url": article.url,
        "raw_text": article.raw_text,
        "raw_text_preview": article.raw_text_preview,
        "source_priority": article.source_priority,
        "source_type": article.source_type,
        "source_domain": article.source_domain,
        "tags": article.tags,
        "extraction_warning": article.extraction_warning,
    }


def _extraction_warning(title: str, raw_text: str, published_date: str) -> str:
    warnings = []
    if not title:
        warnings.append("missing_title")
    if len(raw_text) < 200:
        warnings.append("short_text")
    if not published_date:
        warnings.append("missing_or_unparsed_date")
    return "; ".join(warnings)


def _merge_warnings(*values: object) -> str:
    parts = []
    for value in values:
        for part in str(value or "").replace("；", ";").split(";"):
            part = part.strip()
            if part and part not in parts:
                parts.append(part)
    return "; ".join(parts)
