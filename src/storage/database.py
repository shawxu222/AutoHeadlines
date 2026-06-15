from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.config_loader import DATA_ROOT
from src.fetchers.base import Article
from src.utils.logger import get_logger


logger = get_logger(__name__)
DB_PATH = DATA_ROOT / "processed" / "digest.sqlite3"


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    db_path = db_path or DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT NOT NULL,
                title_original TEXT,
                source TEXT,
                country_region TEXT,
                language TEXT,
                published_date TEXT,
                url TEXT,
                raw_text TEXT,
                source_priority INTEGER,
                source_type TEXT,
                source_domain TEXT,
                extraction_warning TEXT,
                tags_json TEXT,
                fetched_at TEXT,
                UNIQUE(run_date, url)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS candidates (
                candidate_id TEXT NOT NULL,
                run_date TEXT NOT NULL,
                score REAL,
                recommended_reason TEXT,
                title_original TEXT,
                title_translated_candidate TEXT,
                source TEXT,
                country_region TEXT,
                language TEXT,
                published_date TEXT,
                url TEXT,
                matched_keywords TEXT,
                suggested_type TEXT,
                suggested_soft_hard TEXT,
                raw_text_preview TEXT,
                raw_text TEXT,
                source_domain TEXT,
                reference_similarity_score REAL,
                duplicate_group TEXT,
                extraction_warning TEXT,
                selected TEXT,
                notes TEXT,
                PRIMARY KEY (run_date, candidate_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS digests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT NOT NULL,
                candidate_id TEXT,
                order_index INTEGER,
                title_cn TEXT,
                summary_cn TEXT,
                keywords_json TEXT,
                type TEXT,
                soft_hard TEXT,
                source TEXT,
                url TEXT
            )
            """
        )
        _ensure_column(connection, "articles", "source_domain", "TEXT")
        _ensure_column(connection, "articles", "extraction_warning", "TEXT")
        _ensure_column(connection, "candidates", "source_domain", "TEXT")
        _ensure_column(connection, "candidates", "reference_similarity_score", "REAL")
        _ensure_column(connection, "candidates", "extraction_warning", "TEXT")
        _migrate_candidates_primary_key(connection)


def _ensure_column(
    connection: sqlite3.Connection, table_name: str, column_name: str, column_type: str
) -> None:
    columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in columns:
        connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
        )


def _migrate_candidates_primary_key(connection: sqlite3.Connection) -> None:
    """Allow the same article to be reviewed independently on different run dates."""
    columns = connection.execute("PRAGMA table_info(candidates)").fetchall()
    primary_key = [
        row["name"]
        for row in sorted(columns, key=lambda item: item["pk"])
        if row["pk"]
    ]
    if primary_key == ["run_date", "candidate_id"]:
        return

    connection.execute(
        """
        CREATE TABLE candidates_v2 (
            candidate_id TEXT NOT NULL,
            run_date TEXT NOT NULL,
            score REAL,
            recommended_reason TEXT,
            title_original TEXT,
            title_translated_candidate TEXT,
            source TEXT,
            country_region TEXT,
            language TEXT,
            published_date TEXT,
            url TEXT,
            matched_keywords TEXT,
            suggested_type TEXT,
            suggested_soft_hard TEXT,
            raw_text_preview TEXT,
            raw_text TEXT,
            source_domain TEXT,
            reference_similarity_score REAL,
            duplicate_group TEXT,
            extraction_warning TEXT,
            selected TEXT,
            notes TEXT,
            PRIMARY KEY (run_date, candidate_id)
        )
        """
    )
    connection.execute(
        """
        INSERT OR REPLACE INTO candidates_v2
        SELECT candidate_id, run_date, score, recommended_reason, title_original,
               title_translated_candidate, source, country_region, language,
               published_date, url, matched_keywords, suggested_type,
               suggested_soft_hard, raw_text_preview, raw_text, source_domain,
               reference_similarity_score, duplicate_group, extraction_warning,
               selected, notes
        FROM candidates
        """
    )
    connection.execute("DROP TABLE candidates")
    connection.execute("ALTER TABLE candidates_v2 RENAME TO candidates")


def save_articles(run_date: str, articles: list[Article]) -> int:
    init_db()
    inserted = 0
    with get_connection() as connection:
        for article in articles:
            try:
                payload = asdict(article)
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO articles (
                        run_date, title_original, source, country_region, language,
                        published_date, url, raw_text, source_priority, source_type,
                        source_domain, extraction_warning, tags_json, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_date,
                        payload["title_original"],
                        payload["source"],
                        payload["country_region"],
                        payload["language"],
                        payload["published_date"],
                        payload["url"],
                        payload["raw_text"],
                        payload["source_priority"],
                        payload["source_type"],
                        payload.get("source_domain", ""),
                        payload.get("extraction_warning", ""),
                        json.dumps(payload["tags"], ensure_ascii=False),
                        payload["fetched_at"],
                    ),
                )
                inserted += cursor.rowcount
            except Exception as exc:
                logger.exception("Failed to save article %s: %s", article.url, exc)
    return inserted


def clear_run_date(run_date: str) -> None:
    """Clear one day's generated data so repeated MVP tests are reproducible."""
    init_db()
    with get_connection() as connection:
        connection.execute("DELETE FROM articles WHERE run_date = ?", (run_date,))
        connection.execute("DELETE FROM candidates WHERE run_date = ?", (run_date,))
        connection.execute("DELETE FROM digests WHERE run_date = ?", (run_date,))


def load_articles(run_date: str) -> list[dict[str, Any]]:
    init_db()
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM articles WHERE run_date = ? ORDER BY id", (run_date,)
        ).fetchall()
    return [dict(row) for row in rows]


def load_known_urls(exclude_run_date: str | None = None) -> set[str]:
    init_db()
    with get_connection() as connection:
        if exclude_run_date:
            rows = connection.execute(
                """
                SELECT url FROM articles
                WHERE url IS NOT NULL AND url != '' AND run_date != ?
                """,
                (exclude_run_date,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT url FROM articles WHERE url IS NOT NULL AND url != ''"
            ).fetchall()
    return {str(row["url"]) for row in rows}


def save_candidates(run_date: str, candidates: list[dict[str, Any]]) -> None:
    init_db()
    with get_connection() as connection:
        connection.execute("DELETE FROM candidates WHERE run_date = ?", (run_date,))
        for candidate in candidates:
            try:
                connection.execute(
                    """
                    INSERT INTO candidates (
                        candidate_id, run_date, score, recommended_reason,
                        title_original, title_translated_candidate, source,
                        country_region, language, published_date, url,
                        matched_keywords, suggested_type, suggested_soft_hard,
                        raw_text_preview, raw_text, source_domain,
                        reference_similarity_score, duplicate_group,
                        extraction_warning, selected, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate.get("candidate_id"),
                        run_date,
                        candidate.get("score"),
                        candidate.get("recommended_reason"),
                        candidate.get("title_original"),
                        candidate.get("title_translated_candidate", ""),
                        candidate.get("source"),
                        candidate.get("country_region"),
                        candidate.get("language"),
                        candidate.get("published_date"),
                        candidate.get("url"),
                        candidate.get("matched_keywords"),
                        candidate.get("suggested_type"),
                        candidate.get("suggested_soft_hard"),
                        candidate.get("raw_text_preview"),
                        candidate.get("raw_text"),
                        candidate.get("source_domain", ""),
                        candidate.get("reference_similarity_score", 0),
                        candidate.get("duplicate_group", ""),
                        candidate.get("extraction_warning", ""),
                        candidate.get("selected", ""),
                        candidate.get("notes", ""),
                    ),
                )
            except Exception as exc:
                logger.exception(
                    "Failed to save candidate %s: %s",
                    candidate.get("candidate_id"),
                    exc,
                )


def load_candidates(run_date: str) -> list[dict[str, Any]]:
    init_db()
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM candidates WHERE run_date = ? ORDER BY score DESC",
            (run_date,),
        ).fetchall()
    return [dict(row) for row in rows]


def replace_digests(run_date: str, digests: list[dict[str, Any]]) -> None:
    init_db()
    with get_connection() as connection:
        connection.execute("DELETE FROM digests WHERE run_date = ?", (run_date,))
        for index, digest in enumerate(digests, start=1):
            connection.execute(
                """
                INSERT INTO digests (
                    run_date, candidate_id, order_index, title_cn, summary_cn,
                    keywords_json, type, soft_hard, source, url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_date,
                    digest.get("candidate_id"),
                    index,
                    digest.get("title_cn"),
                    digest.get("summary_cn"),
                    json.dumps(digest.get("keywords", []), ensure_ascii=False),
                    digest.get("type"),
                    digest.get("soft_hard"),
                    digest.get("source"),
                    digest.get("url"),
                ),
            )


def load_digests(run_date: str) -> list[dict[str, Any]]:
    init_db()
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM digests WHERE run_date = ? ORDER BY order_index",
            (run_date,),
        ).fetchall()
    output = []
    for row in rows:
        item = dict(row)
        try:
            item["keywords"] = json.loads(item.pop("keywords_json") or "[]")
        except Exception:
            item["keywords"] = []
        output.append(item)
    return output
