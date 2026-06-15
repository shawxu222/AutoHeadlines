from __future__ import annotations

from src.fetchers.html_fetcher import HTMLFetcher


class LoginBrowserFetcher(HTMLFetcher):
    """Placeholder for login-backed browser fetchers.

    The automatic public pipeline does not use this class. Nikkei has a separate
    conservative Playwright implementation in nikkei_fetcher.py.
    """
