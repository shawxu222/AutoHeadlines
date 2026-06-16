from __future__ import annotations

import json
from pathlib import Path

from src.collectors import reported_history
from src.collectors.reported_history import (
    filter_reported_items,
    load_reported_urls,
    normalize_reported_url,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_normalize_reported_url_removes_tracking_noise() -> None:
    assert (
        normalize_reported_url(
            "HTTPS://Example.com/news/123/?utm_source=mail&n_cid=top&keep=1#fragment"
        )
        == "https://example.com/news/123?keep=1"
    )


def test_normalize_reported_url_removes_jsessionid() -> None:
    assert (
        normalize_reported_url(
            "https://www.msit.go.kr/eng/bbs/view.do;jsessionid=ABC123"
            "?sCode=eng&nttSeqNo=1264"
        )
        == "https://www.msit.go.kr/eng/bbs/view.do?sCode=eng&nttSeqNo=1264"
    )


def test_load_reported_urls_only_uses_previous_final_digests(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(reported_history, "DATA_ROOT", tmp_path / "data")
    output_dir = tmp_path / "data" / "output"
    _write_json(
        output_dir / "final_digest_20260602.json",
        [{"url": "https://example.com/news/old/?utm_source=mail"}],
    )
    _write_json(
        output_dir / "final_digest_20260603.json",
        [{"url": "https://example.com/news/current"}],
    )

    urls = load_reported_urls(before_date="2026-06-03")

    assert "https://example.com/news/old" in urls
    assert "https://example.com/news/current" not in urls


def test_load_reported_urls_reads_cumulative_blocks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(reported_history, "DATA_ROOT", tmp_path / "data")
    output_dir = tmp_path / "data" / "output"
    _write_json(
        output_dir / "cumulative_digest.json",
        {
            "date_blocks": [
                {
                    "date": "2026-06-02",
                    "items": [{"url": "https://example.com/news/from-cumulative/"}],
                },
                {
                    "date": "2026-06-03",
                    "items": [{"url": "https://example.com/news/current"}],
                },
            ]
        },
    )

    urls = load_reported_urls(before_date="2026-06-03")

    assert "https://example.com/news/from-cumulative" in urls
    assert "https://example.com/news/current" not in urls


def test_filter_reported_items_removes_previously_reported_urls() -> None:
    rows = [
        {"url": "https://example.com/news/old?utm_campaign=daily", "title": "old"},
        {"url": "https://example.com/news/new", "title": "new"},
        {"url": "", "title": "manual"},
    ]

    filtered = filter_reported_items(
        rows,
        reported_urls={"https://example.com/news/old"},
    )

    assert [row["title"] for row in filtered] == ["new", "manual"]
