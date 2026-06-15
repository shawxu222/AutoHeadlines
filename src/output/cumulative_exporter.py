from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config_loader import DATA_ROOT
from src.llm.digest_generator import load_final_json
from src.output.word_writer import write_cumulative_docx
from src.utils.dates import iso_date


def cumulative_json_path() -> Path:
    return DATA_ROOT / "output" / "cumulative_digest.json"


def export_cumulative(run_date: str) -> tuple[Path, Path, list[dict[str, Any]]]:
    """Merge single-day digests into cumulative JSON and Word outputs."""
    json_path, blocks = merge_final_json_into_cumulative(run_date)
    docx_path = write_cumulative_docx(blocks)
    return json_path, docx_path, blocks


def merge_final_json_into_cumulative(run_date: str) -> tuple[Path, list[dict[str, Any]]]:
    """Merge a single-day final JSON into the cumulative JSON without writing Word."""
    date_key = iso_date(run_date)
    blocks = load_cumulative_json()
    if not blocks:
        blocks = _load_all_single_day_blocks()

    current_items = load_final_json(date_key)
    if not current_items:
        raise FileNotFoundError(f"No final digest JSON found for {date_key}")

    blocks = [block for block in blocks if block.get("date") != date_key]
    blocks.append({"date": date_key, "items": current_items})
    blocks = _normalize_blocks(blocks)

    json_path = save_cumulative_json(blocks)
    return json_path, blocks


def load_cumulative_json() -> list[dict[str, Any]]:
    path = cumulative_json_path()
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("date_blocks", [])
    return []


def save_cumulative_json(blocks: list[dict[str, Any]]) -> Path:
    path = cumulative_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"date_blocks": blocks}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _load_all_single_day_blocks() -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for path in sorted((DATA_ROOT / "output").glob("final_digest_*.json")):
        date_key = _date_from_final_json_path(path)
        if not date_key:
            continue
        items = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(items, list) and items:
            blocks.append({"date": date_key, "items": items})
    return blocks


def _date_from_final_json_path(path: Path) -> str | None:
    stem = path.stem
    raw = stem.replace("final_digest_", "")
    if len(raw) != 8 or not raw.isdigit():
        return None
    return datetime.strptime(raw, "%Y%m%d").date().isoformat()


def _normalize_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_urls: set[str] = set()
    normalized: list[dict[str, Any]] = []

    for block in sorted(blocks, key=lambda item: item.get("date", "")):
        date_key = iso_date(str(block.get("date", "")))
        items = []
        for item in block.get("items", []):
            url = str(item.get("url", "")).strip()
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            items.append(item)
        if items:
            normalized.append({"date": date_key, "items": items})
    return normalized
