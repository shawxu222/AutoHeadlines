from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.config_loader import DATA_ROOT
from src.parsers.text_cleaner import clean_text
from src.utils.dates import iso_date


TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "yclid",
    "mc_cid",
    "mc_eid",
    "n_cid",
    "ref",
    "referrer",
    "source",
}


def load_reported_urls(before_date: str | None = None) -> set[str]:
    """Return normalized URLs that have already been included in final digests."""
    output_dir = DATA_ROOT / "output"
    cutoff = _parse_date(before_date) if before_date else None
    urls: set[str] = set()

    for path in sorted(output_dir.glob("final_digest_*.json")):
        date_key = _date_from_final_json_path(path)
        if not _is_before_cutoff(date_key, cutoff):
            continue
        _add_item_urls(urls, _load_json(path))

    cumulative_path = output_dir / "cumulative_digest.json"
    if cumulative_path.exists():
        for block in _cumulative_blocks(_load_json(cumulative_path)):
            date_key = _parse_date(str(block.get("date", "")))
            if not _is_before_cutoff(date_key, cutoff):
                continue
            _add_item_urls(urls, block.get("items", []))

    return urls


def filter_reported_items(
    items: Iterable[dict[str, Any]],
    *,
    before_date: str | None = None,
    reported_urls: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Remove rows whose URL was already included in a previous final digest."""
    blocked = reported_urls if reported_urls is not None else load_reported_urls(before_date)
    output: list[dict[str, Any]] = []
    for item in items:
        url_key = normalize_reported_url(item.get("url", ""))
        if url_key and url_key in blocked:
            continue
        output.append(dict(item))
    return output


def normalize_reported_url(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    parsed = urlsplit(text)
    if not parsed.scheme or not parsed.netloc:
        return text.rstrip("/")

    query_pairs = [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_tracking_query_key(key)
    ]
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            urlencode(query_pairs, doseq=True),
            "",
        )
    )


def _add_item_urls(urls: set[str], items: Any) -> None:
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized = normalize_reported_url(item.get("url", ""))
        if normalized:
            urls.add(normalized)


def _cumulative_blocks(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [block for block in payload if isinstance(block, dict)]
    if isinstance(payload, dict):
        blocks = payload.get("date_blocks", [])
        if isinstance(blocks, list):
            return [block for block in blocks if isinstance(block, dict)]
    return []


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _date_from_final_json_path(path: Path) -> date | None:
    raw = path.stem.replace("final_digest_", "")
    if len(raw) != 8 or not raw.isdigit():
        return None
    try:
        return datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(iso_date(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def _is_before_cutoff(date_key: date | None, cutoff: date | None) -> bool:
    if cutoff is None:
        return True
    if date_key is None:
        return True
    return date_key < cutoff


def _is_tracking_query_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in TRACKING_QUERY_KEYS or lowered.startswith("utm_")
