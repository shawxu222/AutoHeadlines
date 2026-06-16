from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

from src.config_loader import DATA_ROOT, NewsSource, load_sources
from src.collectors.url_filters import is_feed_url
from src.parsers.article_extractor import DEFAULT_HEADERS
from src.parsers.text_cleaner import clean_text
from src.storage.database import load_known_urls
from src.utils.date_window import collection_window_multiplier, is_in_collection_window
from src.utils.dates import compact_date
from src.utils.jsonl import read_jsonl, write_jsonl
from src.utils.logger import get_logger


logger = get_logger(__name__)

BLOCKED_EXTENSIONS = (
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".zip",
    ".css",
    ".js",
)
NEGATIVE_LINK_HINTS = [
    "login",
    "signup",
    "account",
    "privacy",
    "terms",
    "advert",
    "video",
    "sports",
    "entertainment",
    "recipe",
]
POSITIVE_LINK_HINTS = [
    "news",
    "article",
    "science",
    "technology",
    "tech",
    "press",
    "release",
    "research",
    "policy",
    "industry",
    "economy",
    "202",
    "itmedia",
    "chosun.com/economy/science",
    "biz.chosun.com/science-chosun",
    "biz.chosun.com/it-science",
    "yna.co.kr/industry",
    "msit.go.kr/eng/bbs/view.do",
    "mext.go.jp/b_menu/houdou",
    "mext.go.jp/b_menu/boshu",
    "meti.go.jp/press",
    "meti.go.jp/policy",
    "nedo.go.jp/news",
    "nedo.go.jp/koubo",
    "jst.go.jp/pr",
    "jst.go.jp/kisoken",
    "amed.go.jp/news",
    "amed.go.jp/koubo",
    "nict.go.jp/press",
    "qst.go.jp/site/press",
    "jaxa.jp/press",
    "cao.go.jp/cstp",
]


def discovered_path(run_date: str) -> Path:
    return DATA_ROOT / "processed" / f"discovered_{compact_date(run_date)}.jsonl"


def discover_sources(
    run_date: str,
    include_login_sources: bool = False,
    stop_event: Any | None = None,
) -> list[dict[str, Any]]:
    known_urls = load_known_urls(exclude_run_date=run_date)
    discovered: list[dict[str, Any]] = []
    seen: set[str] = set()
    previous_by_source = _previous_discovered_by_source(run_date)
    sources = [
        source
        for source in load_sources()
        if source.enabled
        and source.source_type != "manual"
        and (include_login_sources or source.source_type != "login_browser")
    ]

    for source in sources:
        if _stop_requested(stop_event):
            logger.info("Source discovery stopped by user request.")
            break
        try:
            source_rows = discover_source(
                source, known_urls | seen, run_date=run_date, stop_event=stop_event
            )
            if _stop_requested(stop_event):
                logger.info("Source discovery stopped by user request.")
                break
            if not source_rows:
                source_rows = _previous_source_rows(
                    source.name,
                    previous_by_source,
                    known_urls | seen,
                )
                if source_rows:
                    logger.info(
                        "Preserved %s previous links from %s after empty discovery.",
                        len(source_rows),
                        source.name,
                    )
            logger.info("Discovered %s links from %s", len(source_rows), source.name)
            for row in source_rows:
                seen.add(row["url"])
            discovered.extend(source_rows)
        except Exception as exc:
            logger.exception("Discovery failed for %s: %s", source.name, exc)

    write_jsonl(discovered_path(run_date), discovered)
    return discovered


def _previous_discovered_by_source(run_date: str) -> dict[str, list[dict[str, Any]]]:
    previous_rows = read_jsonl(discovered_path(run_date))
    output: dict[str, list[dict[str, Any]]] = {}
    for row in previous_rows:
        source = clean_text(row.get("source", ""))
        if source and not is_feed_url(row.get("url", "")):
            output.setdefault(source, []).append(row)
    return output


def _previous_source_rows(
    source_name: str,
    previous_by_source: dict[str, list[dict[str, Any]]],
    known_or_seen_urls: set[str],
) -> list[dict[str, Any]]:
    rows = []
    seen = set(known_or_seen_urls)
    for row in previous_by_source.get(source_name, []):
        url = str(row.get("url", ""))
        if not url or url in seen or is_feed_url(url):
            continue
        seen.add(url)
        rows.append(row)
    return rows


def discover_source(
    source: NewsSource,
    known_urls: set[str] | None = None,
    run_date: str | None = None,
    stop_event: Any | None = None,
) -> list[dict[str, Any]]:
    known_urls = known_urls or set()
    urls = _source_discovery_urls(source, run_date)
    rows: list[dict[str, Any]] = []
    target_limit = _effective_source_limit(source.max_articles_per_run, run_date)
    scan_limit = _source_scan_limit(source.name, target_limit)

    for discovery_url in urls:
        if len(rows) >= scan_limit or _stop_requested(stop_event):
            break
        try:
            if source.source_type == "rss" or discovery_url.endswith((".xml", ".rss")):
                candidates = _discover_from_rss(
                    discovery_url, source, scan_limit, stop_event=stop_event
                )
            else:
                candidates = _discover_from_html(
                    discovery_url, source, scan_limit, stop_event=stop_event
                )
        except Exception as exc:
            logger.exception(
                "Discovery URL failed for %s | %s | %s",
                source.name,
                discovery_url,
                exc,
            )
            continue

        for candidate in candidates:
            if _stop_requested(stop_event):
                break
            url = candidate["url"]
            if url in known_urls or any(row["url"] == url for row in rows):
                continue
            if run_date:
                keep, status = is_in_collection_window(
                    candidate.get("published_date", ""), run_date
                )
                if not keep and status != "missing_published_datetime":
                    continue
            if not _is_relevant_for_source(
                source, url, candidate.get("title_original", "")
            ):
                continue
            rows.append(candidate)
            if len(rows) >= scan_limit:
                break
    return rows


def _effective_source_limit(max_articles_per_run: int, run_date: str | None) -> int:
    multiplier = collection_window_multiplier(run_date) if run_date else 1
    return max(1, min(300, int(max_articles_per_run or 1) * multiplier))


def _discovery_scan_limit(target_limit: int) -> int:
    return max(target_limit, min(300, target_limit * 3))


def _source_scan_limit(source_name: str, target_limit: int) -> int:
    tightly_scoped_sources = {
        "AMED Japan",
        "NICT Japan",
        "QST Japan",
        "Cabinet Office SIP",
        "Cabinet Office Moonshot",
        "Cabinet Office K Program",
        "JAXA Japan",
    }
    if source_name in tightly_scoped_sources:
        return target_limit
    return _discovery_scan_limit(target_limit)


def _discover_from_rss(
    url: str, source: NewsSource, limit: int, stop_event: Any | None = None
) -> list[dict[str, Any]]:
    feed = feedparser.parse(url)
    rows = []
    for entry in feed.entries[:limit]:
        if _stop_requested(stop_event):
            break
        link = clean_text(entry.get("link", ""))
        if not link:
            continue
        rows.append(_discovered_row(source, link, clean_text(entry.get("title", ""))))
    return rows


def _discover_from_html(
    url: str, source: NewsSource, limit: int, stop_event: Any | None = None
) -> list[dict[str, Any]]:
    timeout = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20"))
    response = requests.get(
        url,
        headers=DEFAULT_HEADERS,
        timeout=timeout,
        allow_redirects=True,
    )
    response.raise_for_status()
    if response.apparent_encoding:
        response.encoding = response.apparent_encoding
    elif not response.encoding:
        response.encoding = response.apparent_encoding

    soup = BeautifulSoup(response.text, "html.parser")
    rows: list[dict[str, Any]] = []
    base_domain = urlparse(source.base_url or url).netloc
    selectors = source.link_selectors or ["article a[href]", "main a[href]", "a[href]"]
    for anchor in _select_anchors(soup, selectors):
        if _stop_requested(stop_event):
            break
        href = anchor.get("href", "")
        link = urljoin(url, href)
        parsed = urlparse(link)
        if parsed.scheme not in {"http", "https"}:
            continue
        if base_domain and parsed.netloc and parsed.netloc != base_domain:
            continue
        title = _title_from_anchor(anchor)
        if not _is_relevant_for_source(source, link, title):
            continue
        rows.append(_discovered_row(source, link, title))
        if len(rows) >= limit * 4:
            break
    return _dedupe_by_url(rows)


def _stop_requested(stop_event: Any | None) -> bool:
    return bool(stop_event is not None and stop_event.is_set())


def _source_discovery_urls(source: NewsSource, run_date: str | None) -> list[str]:
    urls = list(source.discovery_urls or [source.section_url])
    year_match = re.search(r"(20\d{2})", str(run_date or ""))
    year = year_match.group(1) if year_match else ""
    urls = [
        url.replace("{year}", year).replace("{run_date}", str(run_date or ""))
        for url in urls
    ]
    if source.name == "AMED Japan" and year:
        urls = [
            f"https://www.amed.go.jp/news/releaselist_{year}_index.html",
            f"https://www.amed.go.jp/news/seika/{year}_seika_index.html",
            f"https://www.amed.go.jp/news/{year}_news_index.html",
            "https://www.amed.go.jp/koubo/keisaibi_index.html",
            *urls,
        ]
    elif source.name == "JAXA Japan" and year:
        urls = [f"https://www.jaxa.jp/press/{year}/index_j.html", *urls]
    elif source.name == "Cabinet Office K Program":
        urls = [
            "https://www8.cao.go.jp/cstp/anzen_anshin/whatsnew_kpro.html",
            *urls,
        ]
    output = []
    seen = set()
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            output.append(url)
    return output


def _is_source_specific_relevant_link(source_name: str, url: str, title: str) -> bool:
    """Keep concrete update pages for sources whose navigation is unusually broad."""
    path = urlparse(url).path.lower()
    if source_name == "AMED Japan":
        return bool(
            re.search(r"/news/release_20\d{6}\.html$", path)
            or (
                "/news/seika/" in path
                and path.endswith(".html")
                and "_index.html" not in path
            )
            or re.search(r"/koubo/\d{2}/\d{2}/[^/]+\.html$", path)
        )
    if source_name == "NICT Japan":
        return bool(re.search(r"/press/20\d{2}/\d{2}/[^/]+\.html$", path))
    if source_name == "QST Japan":
        return bool(re.search(r"/site/press/20\d{6}(?:-\d+)?\.html$", path))
    if source_name == "JAXA Japan":
        return bool(re.search(r"/press/20\d{2}/\d{2}/20\d{6}[^/]*_j\.html$", path))
    if source_name in {
        "Cabinet Office SIP",
        "Cabinet Office Moonshot",
        "Cabinet Office K Program",
    }:
        low_value_paths = [
            "/overview.html",
            "/sip1st_list.html",
            "/sip2nd_list.html",
            "/sip3rd_list.html",
            "/senryakusuishin/suishin.html",
        ]
        if any(path.endswith(item) for item in low_value_paths):
            return False
        combined = f"{title} {path}"
        update_terms = [
            "公募",
            "採択",
            "決定",
            "改定",
            "評価",
            "報告",
            "会議",
            "戦略",
            "社会実装",
            "研究開発ビジョン",
        ]
        return any(term in combined for term in update_terms)
    return True


def _is_relevant_for_source(source: NewsSource, url: str, title: str) -> bool:
    if source.include_url_patterns or source.exclude_url_patterns:
        return _matches_source_patterns(source, url)
    return _is_relevant_link(url, title) and _is_source_specific_relevant_link(
        source.name, url, title
    )


def _matches_source_patterns(source: NewsSource, url: str) -> bool:
    for pattern in source.exclude_url_patterns:
        if _safe_pattern_search(pattern, url):
            return False
    if source.include_url_patterns:
        return any(_safe_pattern_search(pattern, url) for pattern in source.include_url_patterns)
    return True


def _safe_pattern_search(pattern: str, value: str) -> bool:
    try:
        return bool(re.search(pattern, value, flags=re.I))
    except re.error:
        logger.warning("Invalid source URL pattern ignored: %s", pattern)
        return False


def _select_anchors(soup: BeautifulSoup, selectors: list[str]) -> list[Any]:
    anchors = []
    seen = set()
    for selector in selectors:
        try:
            selected = soup.select(selector)
        except Exception:
            logger.warning("Invalid source CSS selector ignored: %s", selector)
            continue
        for anchor in selected:
            identity = id(anchor)
            if identity not in seen:
                seen.add(identity)
                anchors.append(anchor)
    return anchors


def _discovered_row(source: NewsSource, url: str, title: str) -> dict[str, Any]:
    url = _strip_url_session_id(url)
    published_date = _date_from_url(url)
    return {
        "source": source.name,
        "source_type": source.source_type,
        "source_priority": source.priority,
        "country_region": source.country_region,
        "language": source.language,
        "base_url": source.base_url,
        "url": url,
        "source_domain": urlparse(url).netloc,
        "title_original": clean_text(title),
        "published_date": published_date,
        "tags": source.tags,
        "rate_limit_seconds": source.rate_limit_seconds,
        "max_articles_per_run": source.max_articles_per_run,
    }


def _strip_url_session_id(url: str) -> str:
    return re.sub(r";jsessionid=[^/?#]+", "", url, flags=re.I)


def _date_from_url(url: str) -> str:
    path = urlparse(url).path
    match = re.search(r"(?:release_|/site/press/)(20\d{2})(\d{2})(\d{2})", path)
    if match:
        year, month, day = match.groups()
        return f"{year}-{month}-{day}"
    match = re.search(r"/press/(20\d{2})/(\d{2})/(?:20\d{4})?(\d{2})[^/]*\.html$", path)
    if match:
        year, month, day = match.groups()
        return f"{year}-{month}-{day}"
    match = re.search(r"AKR(20\d{2})(\d{2})(\d{2})", url)
    if match:
        year, month, day = match.groups()
        return f"{year}-{month}-{day}"
    match = re.search(r"pt(20\d{2})(\d{2})(\d{2})\d+\.html", path)
    if match:
        year, month, day = match.groups()
        return f"{year}-{month}-{day}"
    match = re.search(r"/articles/(\d{2})(\d{2})/(\d{1,2})/", path)
    if match:
        year, month, day = match.groups()
        return f"20{int(year):02d}-{int(month):02d}-{int(day):02d}"
    match = re.search(r"/(20\d{2})/(\d{1,2})/(\d{1,2})/", path)
    if match:
        year, month, day = match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    return ""


def _title_from_anchor(anchor) -> str:
    title = clean_text(anchor.get_text(" ", strip=True) or anchor.get("title", ""))
    if title:
        return title
    parent = anchor.find_parent()
    for _ in range(4):
        if not parent:
            break
        for selector in ["strong", "h1", "h2", "h3", ".tit", ".title", ".tit-news"]:
            node = parent.select_one(selector) if hasattr(parent, "select_one") else None
            candidate = clean_text(node.get_text(" ", strip=True)) if node else ""
            if candidate:
                return candidate
        candidate = clean_text(parent.get_text(" ", strip=True))
        if candidate:
            return candidate[:140]
        parent = parent.find_parent()
    return ""


def _is_relevant_link(url: str, title: str) -> bool:
    lowered_url = url.lower()
    lowered_title = title.lower()
    parsed = urlparse(url)
    normalized_path = parsed.path.rstrip("/")
    lowered_path = normalized_path.lower()
    domain = parsed.netloc.lower()
    if parsed.fragment:
        return False
    if is_feed_url(url):
        return False
    if lowered_path.endswith("/index.html") and not re.search(r"/20\d{2}/", lowered_path):
        return False
    has_dated_path = bool(re.search(r"/20\d{2}/\d{1,2}/\d{1,2}/", lowered_path))
    if lowered_path.endswith("/") and "/article" not in lowered_path and not has_dated_path:
        return False
    if "eetimes.itmedia.co.jp" in domain and "/articles/" not in lowered_path:
        return False
    if "keguanjp.com" in domain and re.search(r"/pt20\d{10}\.html$", lowered_path):
        return True
    if "chosun.com" in domain and not re.search(r"/20\d{2}/\d{2}/\d{2}/", lowered_path):
        return False
    if "yna.co.kr" in domain and "/view/" not in lowered_path and "/article/" not in lowered_path:
        return False
    if normalized_path in {"", "/index.html", "/news", "/policy", "/industry", "/economy/science"}:
        return False
    if lowered_url.endswith(BLOCKED_EXTENSIONS):
        return False
    if any(hint in lowered_url for hint in NEGATIVE_LINK_HINTS):
        return False
    if lowered_title in {
        "latest news",
        "policy & society",
        "最新뉴스",
        "최신뉴스",
        "전기전자 | 산업 | 연합뉴스",
    }:
        return False
    if any(hint in lowered_url for hint in POSITIVE_LINK_HINTS):
        return True
    if re.search(r"/20\d{2}[/-]?\d{0,2}[/-]?\d{0,2}", lowered_url):
        return True
    return len(title) >= 12 and any(
        word in lowered_title
        for word in ["ai", "半導体", "科学", "技術", "研究", "政策", "산업", "과학", "기술"]
    )


def _dedupe_by_url(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_url: dict[str, dict[str, Any]] = {}
    for row in rows:
        url = row["url"]
        existing = best_by_url.get(url)
        if not existing or len(row.get("title_original", "")) > len(
            existing.get("title_original", "")
        ):
            best_by_url[url] = row
    return list(best_by_url.values())
