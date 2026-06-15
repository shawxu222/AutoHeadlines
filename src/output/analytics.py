from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import pandas as pd

from src.collectors.reported_history import normalize_reported_url
from src.config_loader import DATA_ROOT
from src.output.acceptance_marker import AcceptanceEntry, load_acceptance_entries
from src.output.cumulative_exporter import load_cumulative_json
from src.parsers.text_cleaner import clean_text
from src.utils.dates import iso_date


UNCLASSIFIED = "未分类"
TARGET_DAILY_TOTAL = 10
TARGET_DAILY_ACCEPTED = 2
TARGET_ACCEPTANCE_RATE = 20.0


@dataclass(frozen=True)
class AnalyticsRecord:
    date: str
    order_index: int
    title: str
    url: str
    source: str
    type: str
    soft_hard: str
    keywords: list[str]
    accepted: bool
    data_source: str


def build_analytics_records(master_path: Path | None = None) -> list[AnalyticsRecord]:
    """Build statistics records from the current total Word.

    When the total Word is available, it is the source of truth for which items
    still exist. Digest JSON is only used to enrich those Word rows with type,
    source, and keyword metadata.
    """
    digest_by_key = _digest_metadata_by_key()
    candidate_by_key = _candidate_metadata_by_key()
    word_entries = load_acceptance_entries(master_path) if master_path else []

    if word_entries:
        records = []
        for entry in word_entries:
            key = _record_key(entry.date, entry.title, entry.url)
            metadata = _merge_metadata(
                digest_by_key.get(key),
                candidate_by_key.get(key),
            )
            records.append(_record_from_word_entry(entry, metadata))
        return sorted(records, key=lambda item: (item.date, item.order_index, item.title))

    records = [
        _record_from_digest(digest, accepted=False, data_source="digest_json")
        for digest in digest_by_key.values()
    ]
    return sorted(records, key=lambda item: (item.date, item.order_index, item.title))


def analytics_frame(records: Iterable[AnalyticsRecord]) -> pd.DataFrame:
    rows = []
    for record in records:
        row = asdict(record)
        row["keywords_text"] = "、".join(record.keywords)
        rows.append(row)
    columns = [
        "date",
        "order_index",
        "title",
        "url",
        "source",
        "type",
        "soft_hard",
        "keywords_text",
        "accepted",
        "data_source",
    ]
    return pd.DataFrame(rows, columns=columns).fillna("")


def filter_records_by_date(
    records: Iterable[AnalyticsRecord], start_date: str, end_date: str
) -> list[AnalyticsRecord]:
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    if start is None or end is None:
        return list(records)
    if start > end:
        start, end = end, start
    output = []
    for record in records:
        current = _parse_date(record.date)
        if current is None:
            continue
        if start <= current <= end:
            output.append(record)
    return output


def date_range_for_period(
    period: str,
    anchor_date: str,
    custom_start: str | None = None,
    custom_end: str | None = None,
) -> tuple[str, str]:
    anchor = _parse_date(anchor_date) or date.today()
    if period == "周度":
        start = anchor - timedelta(days=anchor.weekday())
        end = start + timedelta(days=6)
    elif period == "月度":
        start = anchor.replace(day=1)
        next_month = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
        end = next_month - timedelta(days=1)
    elif period == "自定义":
        start = _parse_date(custom_start) or anchor
        end = _parse_date(custom_end) or anchor
    else:
        start = anchor - timedelta(days=anchor.weekday())
        end = start + timedelta(days=6)
    if start > end:
        start, end = end, start
    return start.isoformat(), end.isoformat()


def summary_metrics(records: Iterable[AnalyticsRecord]) -> dict[str, Any]:
    rows = list(records)
    total = len(rows)
    accepted = sum(1 for row in rows if row.accepted)
    rate = round(accepted / total * 100, 1) if total else 0.0
    return {
        "total": total,
        "accepted": accepted,
        "acceptance_rate": rate,
        "unaccepted": total - accepted,
    }


def target_metrics_for_period(
    start_date: str,
    end_date: str,
    anchor_date: str | None = None,
) -> dict[str, Any]:
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    anchor = _parse_date(anchor_date) if anchor_date else None
    if start is None or end is None:
        return {
            "start_date": start_date,
            "end_date": end_date,
            "reporting_days": 0,
            "total": 0,
            "accepted": 0,
            "acceptance_rate": TARGET_ACCEPTANCE_RATE,
        }
    if start > end:
        start, end = end, start
    target_end = end
    if anchor and start <= anchor <= end:
        target_end = anchor
    reporting_days = _weekday_count(start, target_end)
    return {
        "start_date": start.isoformat(),
        "end_date": target_end.isoformat(),
        "reporting_days": reporting_days,
        "total": reporting_days * TARGET_DAILY_TOTAL,
        "accepted": reporting_days * TARGET_DAILY_ACCEPTED,
        "acceptance_rate": TARGET_ACCEPTANCE_RATE,
    }


def daily_counts_frame(records: Iterable[AnalyticsRecord]) -> pd.DataFrame:
    frame = analytics_frame(records)
    if frame.empty:
        return pd.DataFrame(columns=["date", "总摘录数", "被采纳数"])
    grouped = (
        frame.assign(accepted_int=frame["accepted"].astype(bool).astype(int))
        .groupby("date", as_index=False)
        .agg(总摘录数=("title", "count"), 被采纳数=("accepted_int", "sum"))
        .sort_values("date")
    )
    return grouped


def distribution_frame(records: Iterable[AnalyticsRecord], column: str) -> pd.DataFrame:
    frame = analytics_frame(records)
    if frame.empty or column not in frame.columns:
        return pd.DataFrame(columns=[column, "总摘录数", "被采纳数", "采纳率"])
    grouped = (
        frame.assign(accepted_int=frame["accepted"].astype(bool).astype(int))
        .groupby(column, as_index=False)
        .agg(总摘录数=("title", "count"), 被采纳数=("accepted_int", "sum"))
    )
    grouped["采纳率"] = grouped.apply(
        lambda row: round(row["被采纳数"] / row["总摘录数"] * 100, 1)
        if row["总摘录数"]
        else 0,
        axis=1,
    )
    return grouped.sort_values(["总摘录数", "被采纳数"], ascending=False)


def keyword_counts(records: Iterable[AnalyticsRecord], limit: int = 10) -> pd.DataFrame:
    counter: Counter[str] = Counter()
    accepted_counter: Counter[str] = Counter()
    for record in records:
        for keyword in record.keywords:
            word = clean_text(keyword)
            if not word:
                continue
            counter[word] += 1
            if record.accepted:
                accepted_counter[word] += 1
    rows = [
        {
            "关键词": keyword,
            "出现次数": count,
            "被采纳次数": accepted_counter[keyword],
        }
        for keyword, count in counter.most_common(limit)
    ]
    return pd.DataFrame(rows, columns=["关键词", "出现次数", "被采纳次数"]).fillna("")


def export_analytics_excel(records: Iterable[AnalyticsRecord], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame = analytics_frame(records)
    daily = daily_counts_frame(records)
    by_type = distribution_frame(records, "type")
    by_soft_hard = distribution_frame(records, "soft_hard")
    by_source = distribution_frame(records, "source")
    keywords = keyword_counts(records, limit=30)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="明细", index=False)
        daily.to_excel(writer, sheet_name="每日趋势", index=False)
        by_type.to_excel(writer, sheet_name="类型分布", index=False)
        by_soft_hard.to_excel(writer, sheet_name="软硬科学", index=False)
        by_source.to_excel(writer, sheet_name="来源分布", index=False)
        keywords.to_excel(writer, sheet_name="关键词", index=False)
    return output_path


def analytics_export_path(start_date: str, end_date: str) -> Path:
    start = start_date.replace("-", "")
    end = end_date.replace("-", "")
    return DATA_ROOT / "output" / f"analytics_{start}_{end}.xlsx"


def _digest_metadata_by_key() -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for block in _load_digest_blocks():
        date_key = _safe_iso_date(block.get("date", ""))
        if not date_key:
            continue
        for order_index, item in enumerate(block.get("items", []), start=1):
            if not isinstance(item, dict):
                continue
            digest = dict(item)
            digest["_metadata_source"] = "digest_json"
            digest["date"] = date_key
            digest["order_index"] = int(digest.get("order_index") or order_index)
            key = _record_key(
                date_key,
                _digest_title(digest),
                str(digest.get("url", "")),
            )
            if key:
                rows[key] = digest
    return rows


def _candidate_metadata_by_key() -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for item in _candidate_metadata_rows_from_excels() + _candidate_metadata_rows_from_db():
        keys = _metadata_keys_for_item(item)
        for key in keys:
            old = rows.get(key)
            if old is None or _metadata_quality(item) > _metadata_quality(old):
                rows[key] = item
    return rows


def _candidate_metadata_rows_from_excels() -> list[dict[str, Any]]:
    output_dir = DATA_ROOT / "output"
    rows: list[dict[str, Any]] = []
    for path in sorted(output_dir.glob("*candidates_*.xlsx")):
        date_key = _date_from_candidate_path(path)
        try:
            frame = pd.read_excel(path).fillna("")
        except Exception:
            continue
        for _, row in frame.iterrows():
            item = _candidate_metadata_from_mapping(row.to_dict(), date_key)
            if item:
                rows.append(item)
    return rows


def _candidate_metadata_rows_from_db() -> list[dict[str, Any]]:
    db_path = DATA_ROOT / "processed" / "digest.sqlite3"
    if not db_path.exists():
        return []
    try:
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        sql = """
            SELECT run_date, title_original, title_translated_candidate, source, url,
                   matched_keywords, suggested_type, suggested_soft_hard
            FROM candidates
        """
        rows = [
            _candidate_metadata_from_mapping(dict(row), _safe_iso_date(row["run_date"]))
            for row in connection.execute(sql)
        ]
        connection.close()
    except Exception:
        return []
    return [row for row in rows if row]


def _candidate_metadata_from_mapping(
    item: dict[str, Any], date_key: str | None
) -> dict[str, Any]:
    url = clean_text(item.get("url", ""))
    title = clean_text(
        item.get("title_cn")
        or item.get("title_translated_candidate")
        or item.get("title_original")
    )
    if not url and not title:
        return {}
    return {
        "_metadata_source": "candidate_metadata",
        "date": _safe_iso_date(date_key or item.get("run_date", "")),
        "title_cn": title,
        "title_original": clean_text(item.get("title_original", "")),
        "url": url,
        "source": clean_text(item.get("source", "")),
        "type": clean_text(item.get("type") or item.get("suggested_type", "")),
        "soft_hard": clean_text(
            item.get("soft_hard") or item.get("suggested_soft_hard", "")
        ),
        "keywords": item.get("keywords") or item.get("matched_keywords") or "",
    }


def _metadata_keys_for_item(item: dict[str, Any]) -> list[str]:
    date_key = _safe_iso_date(item.get("date", ""))
    url = clean_text(item.get("url", ""))
    titles = {
        clean_text(item.get("title_cn", "")),
        clean_text(item.get("title_original", "")),
    }
    keys = []
    for title in titles:
        key = _record_key(date_key, title, url)
        if key and key not in keys:
            keys.append(key)
    if url:
        key = _record_key("", "", url)
        if key and key not in keys:
            keys.append(key)
    return keys


def _metadata_quality(item: dict[str, Any]) -> int:
    return sum(
        1
        for key in ["source", "type", "soft_hard", "keywords", "url"]
        if not _is_missing_value(item.get(key))
    )


def _merge_metadata(
    primary: dict[str, Any] | None,
    fallback: dict[str, Any] | None,
) -> dict[str, Any]:
    if not primary and not fallback:
        return {}
    merged = dict(primary or {})
    fallback = fallback or {}
    for key, value in fallback.items():
        if key == "_metadata_source":
            continue
        if _is_missing_value(merged.get(key)) and not _is_missing_value(value):
            merged[key] = value
    if primary and fallback:
        merged["_metadata_source"] = "digest_json+candidate_metadata"
    else:
        merged["_metadata_source"] = clean_text(
            (primary or fallback).get("_metadata_source", "")
        )
    return merged


def _load_digest_blocks() -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for block in load_cumulative_json():
        if isinstance(block, dict) and block.get("items"):
            output.append(block)

    output_dir = DATA_ROOT / "output"
    for path in sorted(output_dir.glob("final_digest_*.json")):
        date_key = _date_from_final_json_path(path)
        if not date_key:
            continue
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(items, list) and items:
            output.append({"date": date_key, "items": items})
    return output


def _record_from_word_entry(
    entry: AcceptanceEntry, digest: dict[str, Any] | None
) -> AnalyticsRecord:
    digest = digest or {}
    digest_type = _type_from_metadata_or_title(digest, entry.title, entry.url)
    soft_hard = _soft_hard_from_metadata_or_title(
        digest, entry.title, entry.url, digest_type
    )
    keywords = _normalize_keywords(digest.get("keywords", [])) or _keywords_from_title(
        entry.title
    )
    metadata_source = clean_text(digest.get("_metadata_source", ""))
    data_source = f"word+{metadata_source}" if metadata_source else "word+rules"
    return AnalyticsRecord(
        date=entry.date,
        order_index=entry.order_index,
        title=entry.title,
        url=entry.url or str(digest.get("url", "")),
        source=_source_from_digest_or_url(digest, entry.url),
        type=digest_type,
        soft_hard=soft_hard,
        keywords=keywords,
        accepted=entry.accepted,
        data_source=data_source,
    )


def _record_from_digest(
    digest: dict[str, Any], *, accepted: bool, data_source: str
) -> AnalyticsRecord:
    url = str(digest.get("url", ""))
    return AnalyticsRecord(
        date=_safe_iso_date(digest.get("date", "")),
        order_index=int(digest.get("order_index") or 0),
        title=_digest_title(digest),
        url=url,
        source=_source_from_digest_or_url(digest, url),
        type=_clean_or_unclassified(digest.get("type", "")),
        soft_hard=_clean_or_unclassified(digest.get("soft_hard", "")),
        keywords=_normalize_keywords(digest.get("keywords", [])),
        accepted=accepted,
        data_source=data_source,
    )


def _record_key(date_key: str, title: str, url: str) -> str:
    normalized_url = normalize_reported_url(url)
    if normalized_url:
        return f"url::{normalized_url}"
    title_key = clean_text(title).lower()
    return f"title::{date_key}::{title_key}" if date_key and title_key else ""


def _digest_title(digest: dict[str, Any]) -> str:
    return clean_text(digest.get("title_cn") or digest.get("title_original") or "")


def _normalize_keywords(value: Any) -> list[str]:
    if isinstance(value, list):
        return [clean_text(item) for item in value if clean_text(item)]
    if isinstance(value, str):
        separators = ["、", ",", ";", "；", "|"]
        text = value
        for separator in separators[1:]:
            text = text.replace(separator, separators[0])
        keywords = []
        for item in text.split(separators[0]):
            word = clean_text(item)
            if ":" in word:
                word = clean_text(word.split(":")[-1])
            if "：" in word:
                word = clean_text(word.split("：")[-1])
            if word and word not in keywords:
                keywords.append(word)
        return keywords
    return []


def _clean_or_unclassified(value: Any) -> str:
    return clean_text(value) or UNCLASSIFIED


def _type_from_metadata_or_title(
    metadata: dict[str, Any], title: str, url: str
) -> str:
    value = clean_text(metadata.get("type", ""))
    if value in {"政策", "技术", "产业"}:
        return value
    title_text = clean_text(title)
    if _contains_any(
        title_text,
        ["公募", "募集", "政策", "指南", "管理", "防控", "外流", "计划", "战略", "规制"],
    ):
        return "政策"
    if _contains_any(
        title_text,
        ["投资", "市场", "市值", "量产", "生产", "供应", "企业", "公司", "产业", "订单"],
    ):
        return "产业"
    if title_text or clean_text(url):
        return "技术"
    return UNCLASSIFIED


def _soft_hard_from_metadata_or_title(
    metadata: dict[str, Any], title: str, url: str, digest_type: str
) -> str:
    value = clean_text(metadata.get("soft_hard", ""))
    if value in {"软科学", "硬科学"}:
        return value
    title_text = clean_text(title)
    if digest_type == "政策" and _contains_any(
        title_text,
        ["政策", "指南", "管理", "防控", "外流", "公募", "募集", "战略", "规制"],
    ):
        return "软科学"
    if _contains_any(
        title_text,
        [
            "AI",
            "量子",
            "半导体",
            "材料",
            "临床",
            "试验",
            "新药",
            "脑梗塞",
            "mRNA",
            "心肌梗塞",
            "中微子",
            "富岳",
            "模拟",
            "开发",
            "发现",
            "研发",
            "传感",
            "芯片",
        ],
    ):
        return "硬科学"
    if digest_type in {"技术", "产业"} or clean_text(url):
        return "硬科学"
    return UNCLASSIFIED


def _keywords_from_title(title: str) -> list[str]:
    title_text = clean_text(title)
    candidates = [
        "AI",
        "量子",
        "半导体",
        "材料",
        "临床试验",
        "新药",
        "脑梗塞",
        "mRNA",
        "心肌梗塞",
        "中微子",
        "富岳",
        "技术外流防控",
        "科研支援",
    ]
    return [keyword for keyword in candidates if keyword in title_text][:8]


def _contains_any(text: str, terms: list[str]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _is_missing_value(value: Any) -> bool:
    text = clean_text(value)
    return not text or text in {"未知", UNCLASSIFIED}


def _source_from_digest_or_url(digest: dict[str, Any], url: str) -> str:
    source = clean_text(digest.get("source", ""))
    if source:
        return source
    return _source_from_url(url)


def _source_from_url(url: str) -> str:
    domain = urlparse(str(url)).netloc.lower()
    if not domain:
        return "未知"
    source_map = [
        ("nikkei.com", "Nikkei"),
        ("nedo.go.jp", "NEDO Japan"),
        ("sj.jst.go.jp", "Science Japan"),
        ("jst.go.jp", "JST Japan"),
        ("eetimes.itmedia.co.jp", "EE Times Japan"),
        ("keguanjp.com", "客观日本"),
        ("yna.co.kr", "Yonhap News"),
        ("mext.go.jp", "MEXT Japan"),
        ("meti.go.jp", "METI Japan"),
        ("cao.go.jp", "Cabinet Office Japan"),
        ("cas.go.jp", "Cabinet Secretariat Japan"),
    ]
    for marker, source in source_map:
        if marker in domain:
            return source
    return "未知"


def _safe_iso_date(value: Any) -> str:
    try:
        return iso_date(str(value))
    except Exception:
        return clean_text(value)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(iso_date(str(value)), "%Y-%m-%d").date()
    except ValueError:
        return None


def _weekday_count(start: date, end: date) -> int:
    if start > end:
        return 0
    days = (end - start).days + 1
    return sum(
        1
        for offset in range(days)
        if (start + timedelta(days=offset)).weekday() < 5
    )


def _date_from_final_json_path(path: Path) -> str | None:
    raw = path.stem.replace("final_digest_", "")
    if len(raw) != 8 or not raw.isdigit():
        return None
    try:
        return datetime.strptime(raw, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def _date_from_candidate_path(path: Path) -> str | None:
    for part in path.stem.split("_"):
        if len(part) == 8 and part.isdigit():
            try:
                return datetime.strptime(part, "%Y%m%d").date().isoformat()
            except ValueError:
                return None
    return None
