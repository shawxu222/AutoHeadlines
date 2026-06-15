from __future__ import annotations

from pathlib import Path

from src.storage import database


def test_candidate_id_can_repeat_across_run_dates(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "digest.sqlite3")
    database.init_db()

    candidate = {
        "candidate_id": "same-article",
        "title_original": "A repeatable article",
        "url": "https://example.com/article",
    }
    database.save_candidates("2026-06-11", [candidate])
    database.save_candidates("2026-06-12", [candidate])

    assert len(database.load_candidates("2026-06-11")) == 1
    assert len(database.load_candidates("2026-06-12")) == 1
