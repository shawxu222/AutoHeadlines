from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

from src.config_loader import DATA_ROOT
from src.parsers.text_cleaner import clean_text
from src.utils.logger import get_logger


logger = get_logger(__name__)
DEFAULT_ACCEPTED_PATH = DATA_ROOT / "reference" / "accepted_news.xlsx"
FALLBACK_ACCEPTED_PATH = DATA_ROOT / "input" / "accepted_news.xlsx"


TITLE_COLUMNS = ["title", "标题", "title_cn", "中文标题", "title_original", "原始标题"]
KEYWORD_COLUMNS = ["keywords", "关键词", "matched_keywords"]
TYPE_COLUMNS = ["type", "类型"]
SOFT_HARD_COLUMNS = ["soft_hard", "软/硬科学", "软硬科学"]


def _first_existing(row, names: list[str]) -> str:
    for name in names:
        if name in row and str(row[name]) != "nan":
            return clean_text(str(row[name]))
    return ""


def load_accepted_samples(path: Path = DEFAULT_ACCEPTED_PATH) -> list[dict[str, str]]:
    if not path.exists() and FALLBACK_ACCEPTED_PATH.exists():
        path = FALLBACK_ACCEPTED_PATH
    if not path.exists():
        return []
    try:
        frame = pd.read_excel(path).fillna("")
    except Exception as exc:
        logger.warning("Could not read accepted_news.xlsx: %s", exc)
        return []

    samples = []
    for _, row in frame.iterrows():
        sample = {
            "title": _first_existing(row, TITLE_COLUMNS),
            "keywords": _first_existing(row, KEYWORD_COLUMNS),
            "type": _first_existing(row, TYPE_COLUMNS),
            "soft_hard": _first_existing(row, SOFT_HARD_COLUMNS),
        }
        if sample["title"] or sample["keywords"]:
            samples.append(sample)
    return samples


def title_similarity(title: str, samples: list[dict[str, str]]) -> float:
    title = clean_text(title)
    if not title or not samples:
        return 0.0
    return max(
        SequenceMatcher(None, title, sample.get("title", "")).ratio()
        for sample in samples
    )
