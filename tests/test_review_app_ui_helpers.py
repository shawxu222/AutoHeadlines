from __future__ import annotations

import pandas as pd

from src.ui.review_app import (
    APP_ICON_PATH,
    _apply_acceptance_marker_changes,
    _apply_candidate_editor_changes,
    _filter_excluded_candidate_rows,
    _filter_candidate_editor_frame,
    _lines,
    _merge_candidate_editor_rows,
    _selected_count,
    _source_with_diagnostic_patch,
)


def _candidate_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "candidate_id": "nikkei-ai",
                "selected": False,
                "score": 72.0,
                "source": "Nikkei",
                "title_original": "AI半導体の研究開発",
                "title_translated_candidate": "日本推进AI半导体研发",
                "extraction_warning": "",
                "url": "https://example.com/nikkei-ai",
            },
            {
                "candidate_id": "amed-drug",
                "selected": False,
                "score": 55.0,
                "source": "AMED Japan",
                "title_original": "創薬研究の公募",
                "title_translated_candidate": "AMED启动新药研发项目",
                "extraction_warning": "short_text",
                "url": "https://example.com/amed-drug",
            },
        ]
    )


def test_candidate_filters_can_combine_search_source_score_and_warning() -> None:
    filtered = _filter_candidate_editor_frame(
        _candidate_frame(),
        search_text="新药",
        source_filter="AMED Japan",
        min_score=50,
        warning_only=True,
    )

    assert filtered["candidate_id"].tolist() == ["amed-drug"]


def test_app_icon_exists() -> None:
    assert APP_ICON_PATH.exists()


def test_candidate_editor_merge_preserves_hidden_rows_and_selection() -> None:
    original = _candidate_frame()
    edited_subset = original.iloc[[1]].copy()
    edited_subset.loc[edited_subset.index[0], "selected"] = True

    merged = _merge_candidate_editor_rows(original, edited_subset)

    assert merged["candidate_id"].tolist() == ["nikkei-ai", "amed-drug"]
    assert _selected_count(merged) == 1


def test_candidate_editor_change_is_applied_before_next_rerun() -> None:
    original = _candidate_frame()
    visible_row_keys = [
        {"candidate_id": "amed-drug", "url": "https://example.com/amed-drug"}
    ]

    edited = _apply_candidate_editor_changes(
        original,
        visible_row_keys,
        {"edited_rows": {0: {"selected": True}}},
    )

    assert edited.loc[edited["candidate_id"] == "amed-drug", "selected"].item() is True
    assert _selected_count(edited) == 1


def test_acceptance_marker_change_is_applied_before_fragment_rerun() -> None:
    original = pd.DataFrame(
        [
            {
                "marker_id": "marker-1",
                "accepted": False,
                "date": "2026-06-16",
                "title": "1.重要科技政策",
            },
            {
                "marker_id": "",
                "accepted": False,
                "date": "",
                "title": "",
            },
        ]
    )

    edited = _apply_acceptance_marker_changes(
        original,
        ["marker-1", ""],
        {"edited_rows": {0: {"accepted": True}, 1: {"accepted": True}}},
    )

    assert (
        bool(edited.loc[edited["marker_id"] == "marker-1", "accepted"].item())
        is True
    )
    assert bool(edited.loc[edited["marker_id"] == "", "accepted"].item()) is False


def test_existing_candidate_frame_hides_excluded_sports_rows() -> None:
    frame = _candidate_frame()
    sports = frame.iloc[[0]].copy()
    sports.loc[sports.index[0], "candidate_id"] = "golf"
    sports.loc[sports.index[0], "title_original"] = "米男子ゴルフ最終日"
    combined = pd.concat([frame, sports], ignore_index=True)

    filtered = _filter_excluded_candidate_rows(combined)

    assert filtered["candidate_id"].tolist() == ["nikkei-ai", "amed-drug"]


def test_source_diagnostic_patch_is_applied_before_adding() -> None:
    source = {"name": "Example", "source_type": "html"}
    report = {
        "suggested_patch": {
            "source_type": "rss",
            "discovery_urls": ["https://example.com/rss.xml"],
        }
    }

    assert _source_with_diagnostic_patch(source, report)["source_type"] == "rss"
    assert _lines("article a[href]\n\nmain a[href]") == [
        "article a[href]",
        "main a[href]",
    ]
