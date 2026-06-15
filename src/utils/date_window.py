from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any

from src.config_loader import load_profile
from src.utils.dates import parse_run_date


def collection_window(run_date: str | date) -> tuple[datetime, datetime]:
    """Return the reporting window configured by the active profile."""
    profile = load_profile()
    run_day = parse_run_date(run_date) if isinstance(run_date, str) else run_date
    days_back = (
        profile.monday_lookback_days
        if run_day.weekday() == 0
        else profile.normal_lookback_days
    )
    start_day = run_day - timedelta(days=days_back)
    start = datetime.combine(start_day, time(hour=profile.window_start_hour))
    end = datetime.combine(run_day, time(hour=23, minute=59, second=59))
    return start, end


def collection_window_multiplier(run_date: str | date) -> int:
    """Return a small multiplier for fetch limits based on the reporting window.

    Monday reports cover Friday 10:00 onward, so each source needs a deeper
    candidate pool than a normal one-day report. Keep this intentionally simple
    and predictable for non-technical users: Monday = 3x, other days = 1x.
    """
    profile = load_profile()
    run_day = parse_run_date(run_date) if isinstance(run_date, str) else run_date
    return profile.monday_lookback_days if run_day.weekday() == 0 else 1


def collection_window_label(run_date: str | date) -> str:
    profile = load_profile()
    start, end = collection_window(run_date)
    return f"{start:%Y-%m-%d %H:%M} 至 {end:%Y-%m-%d %H:%M}（{profile.timezone_label}）"


def is_in_collection_window(value: Any, run_date: str | date) -> tuple[bool, str]:
    """Check whether a published date/time belongs to the reporting window.

    Date-only values on the boundary day are kept with a warning because many
    sites expose only the day, not the exact publish time.
    """
    parsed, has_time = parse_article_datetime(value)
    if parsed is None:
        return False, "missing_published_datetime"

    start, end = collection_window(run_date)
    if has_time:
        if start <= parsed <= end:
            return True, "within_collection_window"
        return False, f"outside_collection_window:{start:%Y-%m-%d %H:%M}"

    article_day = parsed.date()
    if start.date() < article_day <= end.date():
        return True, "within_collection_window_date_only"
    if article_day == start.date():
        return True, "boundary_date_without_time"
    return False, f"outside_collection_window:{start:%Y-%m-%d %H:%M}"


def append_window_warning(existing: Any, status: str) -> str:
    if status in {"within_collection_window", "within_collection_window_date_only"}:
        return str(existing or "")
    parts = [part.strip() for part in str(existing or "").split(";") if part.strip()]
    if status not in parts:
        parts.append(status)
    return "; ".join(parts)


def parse_article_datetime(value: Any) -> tuple[datetime | None, bool]:
    text = str(value or "").strip()
    if not text:
        return None, False

    era_match = re.search(
        r"(令和|平成|昭和)\s*(元|\d{1,2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
        r"(?:[ T曜　]*(\d{1,2}):(\d{2}))?",
        text,
    )
    if era_match:
        era, era_year_text, month, day, hour, minute = era_match.groups()
        era_base = {"令和": 2018, "平成": 1988, "昭和": 1925}[era]
        era_year = 1 if era_year_text == "元" else int(era_year_text)
        year = era_base + era_year
        if hour and minute:
            parsed = _safe_datetime(year, int(month), int(day), int(hour), int(minute))
            return (parsed, True) if parsed else (None, False)
        parsed = _safe_datetime(year, int(month), int(day))
        return (parsed, False) if parsed else (None, False)

    text = (
        text.replace("年", "-")
        .replace("月", "-")
        .replace("日", " ")
        .replace("/", "-")
        .replace(".", "-")
    )

    patterns = [
        (r"(20\d{2})-(\d{1,2})-(\d{1,2})[ T曜　]*(\d{1,2}):(\d{2})", True),
        (r"(20\d{2})-(\d{1,2})-(\d{1,2})", False),
    ]
    for pattern, has_time in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        groups = [int(group) for group in match.groups()]
        if has_time:
            year, month, day, hour, minute = groups
            parsed = _safe_datetime(year, month, day, hour, minute)
            return (parsed, True) if parsed else (None, False)
        year, month, day = groups
        parsed = _safe_datetime(year, month, day)
        return (parsed, False) if parsed else (None, False)
    return None, False


def _safe_datetime(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0
) -> datetime | None:
    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None
