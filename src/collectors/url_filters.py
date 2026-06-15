from __future__ import annotations

from urllib.parse import urlparse


def is_feed_url(url: object) -> bool:
    path = urlparse(str(url or "")).path.lower()
    return path.endswith((".xml", ".rss")) or "/rss/" in path
