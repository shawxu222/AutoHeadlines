from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from urllib.parse import urlparse

from src.config_loader import NewsSource


@dataclass(slots=True)
class Article:
    title_original: str
    source: str
    country_region: str
    language: str
    published_date: str
    url: str
    raw_text: str
    source_priority: int
    source_type: str
    tags: list[str] = field(default_factory=list)
    source_domain: str = ""
    extraction_warning: str = ""
    fetched_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    @property
    def raw_text_preview(self) -> str:
        return self.raw_text[:500].strip()

    def __post_init__(self) -> None:
        if not self.source_domain and self.url:
            self.source_domain = urlparse(self.url).netloc


class Fetcher(Protocol):
    def fetch(self, source: NewsSource) -> list[Article]:
        ...
