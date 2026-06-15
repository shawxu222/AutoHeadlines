from __future__ import annotations

from src.collectors.selected_fulltext import enrich_selected_candidates


def test_short_selected_text_is_kept_for_digest_generation() -> None:
    enriched, report = enrich_selected_candidates(
        [
            {
                "title_original": "WSTS预测2026年全球半导体市场规模骤增近9成",
                "raw_text_preview": "半导体市场预测。" * 12,
                "url": "",
                "source": "客观日本",
            }
        ]
    )

    assert len(enriched) == 1
    assert report[0]["status"] == "short"
    assert enriched[0]["selected_fulltext_status"] == "short"


def test_selected_title_and_url_are_kept_when_refetch_fails(monkeypatch) -> None:
    def fake_fetch(url, extractor):  # noqa: ANN001
        return {"raw_text": "", "extraction_warning": "selected_fulltext_failed"}

    monkeypatch.setattr(
        "src.collectors.selected_fulltext._fetch_public_full_text",
        fake_fetch,
    )

    enriched, report = enrich_selected_candidates(
        [
            {
                "title_original": "短新闻标题",
                "raw_text_preview": "",
                "url": "https://example.com/article/1",
                "source": "Example",
            }
        ]
    )

    assert len(enriched) == 1
    assert report[0]["status"] == "short"
