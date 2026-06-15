from __future__ import annotations

from pathlib import Path
from typing import Any

from src.collectors.collection import articles_path
from src.collectors.reported_history import filter_reported_items
from src.collectors.url_filters import is_feed_url
from src.config_loader import load_keywords
from src.llm.title_translator import translate_korean_candidate_titles
from src.output.excel_writer import write_candidates_excel
from src.reference_ingestion import load_reference_samples
from src.scoring.candidate_scorer import (
    deduplicate_candidates,
    is_excluded_topic,
    score_candidate,
)
from src.scoring.similarity_scorer import load_accepted_samples
from src.storage.database import save_candidates
from src.utils.date_window import (
    append_window_warning,
    collection_window_multiplier,
    is_in_collection_window,
)
from src.utils.jsonl import read_jsonl


MIN_CANDIDATE_TEXT_CHARS = 250


def build_auto_candidates(
    run_date: str,
    articles_file: Path | None = None,
    stop_event: Any | None = None,
) -> list[dict[str, Any]]:
    articles_file = articles_file or articles_path(run_date)
    articles = read_jsonl(articles_file)
    keywords = load_keywords()
    samples = load_reference_samples() or load_accepted_samples()
    if _stop_requested(stop_event):
        return []
    articles = _filter_articles_by_window(articles, run_date)
    if _stop_requested(stop_event):
        return []
    articles = filter_reported_items(articles, before_date=run_date)
    scored = []
    for article in articles:
        if _stop_requested(stop_event):
            return []
        scored.append(score_candidate(article, keywords, samples))
    if _stop_requested(stop_event):
        return []
    candidates = deduplicate_candidates(scored)
    if _stop_requested(stop_event):
        return []
    candidates = _cap_candidates_by_source_score(candidates, run_date)
    if _stop_requested(stop_event):
        return []
    candidates = translate_korean_candidate_titles(candidates, stop_event=stop_event)
    if _stop_requested(stop_event):
        return []
    save_candidates(run_date, candidates)
    write_candidates_excel(candidates, run_date)
    return candidates


def _stop_requested(stop_event: Any | None) -> bool:
    return bool(stop_event is not None and stop_event.is_set())


def _filter_articles_by_window(
    articles: list[dict[str, Any]], run_date: str
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for article in articles:
        keep, status = is_in_collection_window(article.get("published_date", ""), run_date)
        if not keep:
            continue
        if is_feed_url(article.get("url", "")):
            continue
        text = str(article.get("raw_text") or article.get("raw_text_preview") or "").strip()
        if len(text) < MIN_CANDIDATE_TEXT_CHARS:
            continue
        if is_excluded_topic(article):
            continue
        item = dict(article)
        item["extraction_warning"] = append_window_warning(
            item.get("extraction_warning", ""), status
        )
        filtered.append(item)
    return filtered


def _cap_candidates_by_source_score(
    candidates: list[dict[str, Any]], run_date: str
) -> list[dict[str, Any]]:
    multiplier = collection_window_multiplier(run_date)
    groups: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        source = str(candidate.get("source") or "unknown")
        groups.setdefault(source, []).append(candidate)

    kept: list[dict[str, Any]] = []
    for source, rows in groups.items():
        limit = _candidate_source_limit(rows, multiplier)
        ranked = sorted(rows, key=lambda item: float(item.get("score") or 0), reverse=True)
        kept.extend(ranked[:limit])
    return sorted(kept, key=lambda item: float(item.get("score") or 0), reverse=True)


def _candidate_source_limit(rows: list[dict[str, Any]], multiplier: int) -> int:
    values = []
    for row in rows:
        try:
            value = int(float(row.get("max_articles_per_run") or 0))
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            values.append(value)
    base_limit = max(values) if values else len(rows)
    return max(1, min(len(rows), base_limit * multiplier))
