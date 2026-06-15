from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pandas as pd
from docx import Document

from src.config_loader import DATA_ROOT, load_keywords, reference_docx_path
from src.parsers.text_cleaner import clean_text
from src.scoring.candidate_scorer import suggest_soft_hard, suggest_type
from src.scoring.keyword_scorer import match_keywords
from src.utils.jsonl import write_jsonl
from src.utils.logger import get_logger


logger = get_logger(__name__)
REFERENCE_DIR = DATA_ROOT / "reference"
REFERENCE_NEWS_PATH = DATA_ROOT / "processed" / "reference_news.jsonl"
REFERENCE_KEYWORDS_PATH = DATA_ROOT / "processed" / "reference_keywords.json"
REFERENCE_STATS_PATH = DATA_ROOT / "processed" / "reference_stats.json"
URL_RE = re.compile(r"https?://\S+")
DATE_BLOCK_RE = re.compile(r"每日科技要闻报送摘要(\d{8})")


def ingest_reference(source_docx: Path | None = None) -> tuple[Path, Path, Path]:
    rows = []
    rows.extend(_parse_historical_docx(source_docx or reference_docx_path()))
    accepted_path = REFERENCE_DIR / "accepted_news.xlsx"
    if not accepted_path.exists():
        accepted_path = DATA_ROOT / "input" / "accepted_news.xlsx"
    rows.extend(_parse_accepted_excel(accepted_path))
    rows = _dedupe_reference_rows(rows)

    write_jsonl(REFERENCE_NEWS_PATH, rows)
    stats = _build_stats(rows)
    REFERENCE_STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    REFERENCE_STATS_PATH.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    REFERENCE_KEYWORDS_PATH.write_text(
        json.dumps(stats.get("top_keywords", {}), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return REFERENCE_NEWS_PATH, REFERENCE_KEYWORDS_PATH, REFERENCE_STATS_PATH


def load_reference_samples() -> list[dict[str, str]]:
    if not REFERENCE_NEWS_PATH.exists():
        return []
    samples = []
    with REFERENCE_NEWS_PATH.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            item = json.loads(line)
            samples.append(
                {
                    "title": clean_text(item.get("title_cn") or item.get("title")),
                    "keywords": clean_text(
                        ",".join(item.get("keywords_candidate", []))
                        if isinstance(item.get("keywords_candidate"), list)
                        else item.get("keywords_candidate", "")
                    ),
                    "type": clean_text(item.get("type_candidate", "")),
                    "soft_hard": clean_text(item.get("soft_hard_candidate", "")),
                }
            )
    return samples


def _parse_historical_docx(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        logger.info("Reference docx not found: %s", path)
        return []
    keywords = load_keywords()
    document = Document(path)
    rows = []
    current_date = ""
    current_title = ""
    current_summary_parts: list[str] = []

    def flush(url: str = "") -> None:
        nonlocal current_title, current_summary_parts
        if not current_title:
            return
        summary = clean_text(" ".join(current_summary_parts))
        text = f"{current_title} {summary}"
        matches = match_keywords(text, keywords)
        rows.append(
            {
                "source_file": str(path.name),
                "date_block": current_date,
                "title_cn": current_title,
                "summary_cn": summary,
                "url": url,
                "source_domain": urlparse(url).netloc if url else "",
                "keywords_candidate": [str(item["keyword"]) for item in matches],
                "type_candidate": suggest_type(matches, text),
                "soft_hard_candidate": suggest_soft_hard(matches, text),
            }
        )
        current_title = ""
        current_summary_parts = []

    for paragraph in document.paragraphs:
        raw_text = paragraph.text.strip()
        text = clean_text(raw_text)
        if not text:
            continue
        date_match = DATE_BLOCK_RE.search(text)
        if date_match:
            flush()
            current_date = date_match.group(1)
            continue
        url_match = URL_RE.search(text)
        if url_match:
            prefix = clean_text(text[: url_match.start()])
            if prefix and current_title:
                current_summary_parts.append(prefix)
            flush(url_match.group(0).rstrip("。)）"))
            continue
        if re.match(r"^\d+[.．、]", text):
            flush()
            current_title, inline_summary = _split_numbered_title_summary(raw_text)
            if inline_summary:
                current_summary_parts.append(inline_summary)
        elif current_title:
            current_summary_parts.append(text)
    flush()
    return rows


def _split_numbered_title_summary(text: str) -> tuple[str, str]:
    """Split a numbered item when Word keeps title and summary in one paragraph."""
    text = re.sub(r"^\s*\d+[.．、]\s*", "", text.strip())
    parts = [clean_text(part) for part in re.split(r"[\r\n]+", text) if clean_text(part)]
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])

    compact = clean_text(text)
    sentence_match = re.search(r"(。|；|;)\s*", compact)
    if sentence_match and sentence_match.start() <= 80:
        return compact[: sentence_match.end()].strip(), compact[sentence_match.end() :].strip()
    return compact, ""


def _parse_accepted_excel(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        logger.info("Reference accepted Excel not found: %s", path)
        return []
    try:
        frame = pd.read_excel(path).fillna("")
    except Exception as exc:
        logger.exception("Accepted Excel parsing failed: %s", exc)
        return []

    rows = []
    for _, row in frame.iterrows():
        item = {str(column): row[column] for column in frame.columns}
        title = _pick(item, ["被采纳要闻题目", "标题", "题目", "title", "title_cn"])
        keywords = _pick(item, ["关键词", "keywords", "matched_keywords"])
        if not title and not keywords:
            continue
        rows.append(
            {
                "source_file": str(path.name),
                "date_block": _pick(item, ["日期", "date", "published_date"]),
                "title_cn": clean_text(title),
                "summary_cn": _pick(item, ["摘要", "summary", "summary_cn"]),
                "url": _pick(item, ["URL", "url", "链接"]),
                "source_domain": urlparse(_pick(item, ["URL", "url", "链接"])).netloc,
                "keywords_candidate": keywords,
                "type_candidate": _pick(item, ["类型", "type"]),
                "soft_hard_candidate": _pick(item, ["软/硬科学", "软硬科学", "soft_hard"]),
                "week": _pick(item, ["周次", "week"]),
                "weekly_accepted_count": _pick(item, ["周采纳总数"]),
                "monthly_report_count": _pick(item, ["月采纳/上报数"]),
            }
        )
    return rows


def _pick(item: dict[str, Any], names: list[str]) -> str:
    for name in names:
        if name in item and str(item[name]).strip():
            return clean_text(item[name])
    lowered = {key.lower(): key for key in item}
    for name in names:
        key = lowered.get(name.lower())
        if key and str(item[key]).strip():
            return clean_text(item[key])
    return ""


def _dedupe_reference_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    output = []
    for row in rows:
        key = row.get("url") or row.get("title_cn") or json.dumps(row, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def _build_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    keyword_counter: Counter[str] = Counter()
    domain_counter: Counter[str] = Counter()
    type_counter: Counter[str] = Counter()
    soft_hard_counter: Counter[str] = Counter()
    for row in rows:
        keywords = row.get("keywords_candidate", [])
        if isinstance(keywords, str):
            keywords = [word.strip() for word in re.split(r"[,;；、]", keywords) if word.strip()]
        keyword_counter.update(keywords)
        if row.get("source_domain"):
            domain_counter.update([str(row["source_domain"])])
        if row.get("type_candidate"):
            type_counter.update([str(row["type_candidate"])])
        if row.get("soft_hard_candidate"):
            soft_hard_counter.update([str(row["soft_hard_candidate"])])
    return {
        "reference_count": len(rows),
        "top_keywords": dict(keyword_counter.most_common(100)),
        "top_domains": dict(domain_counter.most_common(50)),
        "type_distribution": dict(type_counter),
        "soft_hard_distribution": dict(soft_hard_counter),
    }
