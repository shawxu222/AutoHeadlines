from __future__ import annotations

from src.utils.date_window import collection_window, is_in_collection_window


def test_weekday_window_starts_previous_day_at_10_japan_time() -> None:
    start, end = collection_window("2026-05-26")
    assert start.strftime("%Y-%m-%d %H:%M") == "2026-05-25 10:00"
    assert end.strftime("%Y-%m-%d %H:%M") == "2026-05-26 23:59"
    assert is_in_collection_window("2026-05-25 09:59", "2026-05-26")[0] is False
    assert is_in_collection_window("2026-05-25 10:00", "2026-05-26")[0] is True


def test_monday_window_starts_previous_friday_at_10_japan_time() -> None:
    start, _ = collection_window("2026-05-25")
    assert start.strftime("%Y-%m-%d %H:%M") == "2026-05-22 10:00"
    assert is_in_collection_window("2026-05-22 09:59", "2026-05-25")[0] is False
    assert is_in_collection_window("2026-05-22 10:00", "2026-05-25")[0] is True


def test_missing_date_is_not_in_collection_window() -> None:
    keep, status = is_in_collection_window("", "2026-05-26")
    assert keep is False
    assert status == "missing_published_datetime"


def test_invalid_article_date_is_treated_as_missing() -> None:
    for value in ["2026-03-00", "2026-06-31", "令和8年2月31日"]:
        keep, status = is_in_collection_window(value, "2026-06-03")
        assert keep is False
        assert status == "missing_published_datetime"
