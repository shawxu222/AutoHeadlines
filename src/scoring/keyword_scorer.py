from __future__ import annotations

from collections import defaultdict

from src.config_loader import KeywordEntry


def match_keywords(text: str, keywords: list[KeywordEntry]) -> list[dict[str, object]]:
    haystack = (text or "").lower()
    matches: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for entry in keywords:
        term = entry.term.strip()
        if not term:
            continue
        key = (entry.category, term)
        if key in seen:
            continue
        if term.lower() in haystack:
            seen.add(key)
            matches.append(
                {
                    "category": entry.category,
                    "keyword": term,
                    "weight": entry.weight,
                }
            )
    return matches


def keyword_score(matches: list[dict[str, object]], max_score: float = 30) -> float:
    raw = sum(float(item.get("weight", 0)) for item in matches)
    return min(max_score, raw)


def matched_keywords_text(matches: list[dict[str, object]]) -> str:
    grouped: dict[str, list[str]] = defaultdict(list)
    for item in matches:
        grouped[str(item["category"])].append(str(item["keyword"]))
    return "; ".join(
        f"{category}: {', '.join(words)}" for category, words in grouped.items()
    )
