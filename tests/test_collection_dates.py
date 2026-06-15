from __future__ import annotations

from src.collectors.collection import _best_published_date, _fallback_published_date


def test_best_published_date_falls_back_to_discovered_date() -> None:
    assert (
        _best_published_date(
            "2026-03-00",
            "2026-06-02",
            "客观日本测试新闻",
            "正文里没有更可靠的日期。",
        )
        == "2026-06-02"
    )


def test_best_published_date_keeps_valid_extracted_datetime() -> None:
    assert (
        _best_published_date(
            "2026/06/02 13:42",
            "2026-06-01",
            "测试新闻",
            "正文",
        )
        == "2026-06-02 13:42"
    )


def test_fallback_published_date_prefers_amed_labeled_update_date() -> None:
    raw_text = (
        "公募開始日 令和8年1月15日。研究開発課題について募集します。"
        "掲載日 令和8年5月18日 最終更新日 令和8年5月19日"
    )

    assert _fallback_published_date("AMEDの公募情報", raw_text) == "2026-05-19"
