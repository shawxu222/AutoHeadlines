from __future__ import annotations

import re


WHITESPACE_RE = re.compile(r"\s+")


def clean_text(value: object | None) -> str:
    """Normalize whitespace while keeping readable sentence boundaries."""
    if not value:
        return ""
    value = str(value)
    text = value.replace("\u3000", " ")
    text = text.replace("\xa0", " ")
    text = WHITESPACE_RE.sub(" ", text)
    return text.strip()


def compact_for_preview(value: str, limit: int = 500) -> str:
    text = clean_text(value)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."
