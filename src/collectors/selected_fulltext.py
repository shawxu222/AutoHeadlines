from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from src.fetchers.nikkei_fetcher import fetch_nikkei_full_text
from src.parsers.article_extractor import ArticleExtractor
from src.parsers.text_cleaner import clean_text
from src.utils.logger import get_logger


logger = get_logger(__name__)
MIN_SELECTED_TEXT_CHARS = 450
MIN_USABLE_SELECTED_TEXT_CHARS = 80


def enrich_selected_candidates(
    candidates: list[dict[str, Any]],
    stop_event: Any | None = None,
    progress_callback: Any | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Re-fetch full text for selected candidates before digest generation."""
    enriched: list[dict[str, Any]] = []
    report: list[dict[str, Any]] = []
    extractor = ArticleExtractor()

    for index, candidate in enumerate(candidates, start=1):
        if stop_event is not None and stop_event.is_set():
            logger.info("Selected full text enrichment stopped by user request.")
            break
        if progress_callback is not None:
            progress_callback(index, len(candidates), candidate)
        item = dict(candidate)
        url = str(item.get("url", "")).strip()
        source = str(item.get("source", ""))
        before_chars = len(_source_text(item))
        fetched: dict[str, Any] = {}

        if url:
            if _is_nikkei_candidate(item):
                fetched = fetch_nikkei_full_text(url, stop_event=stop_event)
            else:
                fetched = _fetch_public_full_text(url, extractor)
        if stop_event is not None and stop_event.is_set():
            logger.info("Selected full text enrichment stopped after current fetch.")
            break

        _merge_fetched_text(item, fetched)
        after_chars = len(_source_text(item))
        ok = after_chars >= MIN_SELECTED_TEXT_CHARS
        usable = _is_usable_selected_item(item, after_chars)
        status = "ok" if ok else "short" if usable else "failed"
        warning = clean_text(fetched.get("extraction_warning", ""))
        if not ok and not warning:
            warning = "selected_fulltext_too_short"
        item["selected_fulltext_status"] = status
        item["selected_fulltext_chars"] = after_chars
        item["selected_fulltext_warning"] = warning
        report.append(
            {
                "title_original": item.get("title_original", ""),
                "source": source,
                "url": url,
                "before_chars": before_chars,
                "after_chars": after_chars,
                "status": status,
                "warning": warning,
            }
        )
        if usable:
            enriched.append(item)

    return enriched, report


def _fetch_public_full_text(url: str, extractor: ArticleExtractor) -> dict[str, Any]:
    try:
        article = extractor.extract_from_url(url)
        return {
            "title_original": article.title,
            "published_date": article.published_date,
            "raw_text": article.text,
            "extraction_warning": "" if len(clean_text(article.text)) >= MIN_SELECTED_TEXT_CHARS else "selected_fulltext_too_short",
        }
    except Exception as exc:
        logger.info("Selected public full text fetch failed for %s: %s", url, exc)
        return {
            "raw_text": "",
            "extraction_warning": f"selected_fulltext_failed: {exc}",
        }


def _merge_fetched_text(item: dict[str, Any], fetched: dict[str, Any]) -> None:
    fetched_text = clean_text(fetched.get("raw_text", ""))
    current_text = _source_text(item)
    if len(fetched_text) > len(current_text):
        item["raw_text"] = fetched_text
        item["raw_text_preview"] = fetched_text[:500]
    elif current_text and not item.get("raw_text"):
        item["raw_text"] = current_text

    for key in ["title_original", "published_date"]:
        value = clean_text(fetched.get(key, ""))
        if value:
            item[key] = value

    warning = clean_text(fetched.get("extraction_warning", ""))
    if warning:
        existing = clean_text(item.get("extraction_warning", ""))
        item["extraction_warning"] = "; ".join(
            part for part in [existing, warning] if part
        )


def _is_nikkei_candidate(item: dict[str, Any]) -> bool:
    source = str(item.get("source", "")).lower()
    domain = urlparse(str(item.get("url", ""))).netloc.lower()
    return source == "nikkei" or domain.endswith("nikkei.com")


def _is_usable_selected_item(item: dict[str, Any], text_chars: int) -> bool:
    if text_chars >= MIN_USABLE_SELECTED_TEXT_CHARS:
        return True
    return bool(clean_text(item.get("title_original", "")) and clean_text(item.get("url", "")))


def _source_text(item: dict[str, Any]) -> str:
    return clean_text(item.get("raw_text", "") or item.get("raw_text_preview", ""))
