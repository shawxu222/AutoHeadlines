from __future__ import annotations

from datetime import date, datetime


def parse_run_date(value: str | None) -> date:
    """Parse a CLI date value, defaulting to today."""
    if not value:
        return date.today()
    return datetime.strptime(value, "%Y-%m-%d").date()


def compact_date(value: date | str) -> str:
    """Return YYYYMMDD for filenames and document titles."""
    if isinstance(value, str):
        value = parse_run_date(value)
    return value.strftime("%Y%m%d")


def iso_date(value: date | str) -> str:
    """Return YYYY-MM-DD for database fields."""
    if isinstance(value, str):
        value = parse_run_date(value)
    return value.isoformat()
