from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import pandas as pd

from src.config_loader import DATA_ROOT, PROJECT_ROOT, load_keywords, load_sources
from src.parsers.text_cleaner import clean_text
from src.reference_ingestion import load_reference_samples
from src.scoring.candidate_scorer import is_excluded_topic, score_candidate
from src.utils.date_window import (
    append_window_warning,
    collection_window_multiplier,
    is_in_collection_window,
)
from src.utils.dates import compact_date
from src.utils.jsonl import write_jsonl
from src.utils.logger import get_logger


logger = get_logger(__name__)
NIKKEI_PROFILE_DIR = Path(
    os.getenv("XAUTOHEADLINES_BROWSER_PROFILE_DIR")
    or os.getenv("AUTOHEADLINES_BROWSER_PROFILE_DIR")
    or PROJECT_ROOT / ".browser_profiles"
).expanduser() / "nikkei"
NIKKEI_HOME = "https://www.nikkei.com/"
NIKKEI_DISCOVERY_URLS = [
    "https://www.nikkei.com/news/",
    "https://www.nikkei.com/news/category/science/",
    "https://www.nikkei.com/science/",
    "https://www.nikkei.com/technology/",
    "https://www.nikkei.com/news/category/technology/",
    "https://www.nikkei.com/business/",
    "https://www.nikkei.com/news/category/business/",
    "https://www.nikkei.com/politics/",
    "https://www.nikkei.com/news/category/politics/",
    "https://www.nikkei.com/economy/",
    "https://www.nikkei.com/news/category/economy/",
    "https://www.nikkei.com/",
]
NIKKEI_PRIORITY_SECTIONS = [
    ("Tech/サイエンス", "https://www.nikkei.com/news/category/science/"),
    ("Tech/サイエンス", "https://www.nikkei.com/science/"),
    ("Tech", "https://www.nikkei.com/technology/"),
    ("Tech", "https://www.nikkei.com/news/category/technology/"),
    ("政治/政策", "https://www.nikkei.com/politics/"),
    ("政治/政策", "https://www.nikkei.com/news/category/politics/"),
    ("ビジネス", "https://www.nikkei.com/business/"),
    ("ビジネス", "https://www.nikkei.com/news/category/business/"),
    ("経済", "https://www.nikkei.com/economy/"),
    ("経済", "https://www.nikkei.com/news/category/economy/"),
    ("速報", "https://www.nikkei.com/news/"),
]
NIKKEI_SECTION_HINTS = [
    "速報",
    "Tech",
    "テック",
    "テクノロジー",
    "科学",
    "Science",
    "サイエンス",
    "技術",
    "政治",
    "政策",
    "Business",
    "ビジネス",
    "企業",
    "経済",
    "スタートアップ",
    "半導体",
    "AI",
]
NIKKEI_NEGATIVE_SECTION_HINTS = [
    "スポーツ",
    "エンタメ",
    "ライフ",
    "グルメ",
    "トラベル",
    "オピニオン",
    "映像",
]
HIGH_VALUE_NIKKEI_TITLE_TERMS = [
    "文部科学省",
    "文科省",
    "経済産業省",
    "経産省",
    "内閣府",
    "jst",
    "nedo",
    "公募",
    "採択",
    "研究支援",
    "研究開発",
    "科学技術",
    "科研",
    "ai使う",
    "ai活用",
    "ai",
    "人工知能",
    "半導体",
    "量子",
    "核融合",
    "宇宙",
    "バイオ",
    "創薬",
    "研究基盤",
]
MIN_NIKKEI_BODY_CHARS = 450
MAX_NIKKEI_SECTION_PAGES = 16
NIKKEI_NAV_TIMEOUT_MS = 45000
NIKKEI_NAV_RETRY_ATTEMPTS = 3
NIKKEI_NAV_RETRY_WAIT_MS = 3000
INSTALL_PLAYWRIGHT_CMD = "python -m pip install playwright"
INSTALL_CHROMIUM_CMD = "python -m playwright install chromium"
NIKKEI_COLUMNS = [
    "title_original",
    "published_date",
    "url",
    "source",
    "source_section",
    "raw_text_preview",
    "extraction_warning",
    "logged_in_status",
    "score",
    "recommended_reason",
    "matched_keywords",
]


def check_browser_env() -> dict[str, Any]:
    """Check whether the dedicated Nikkei browser automation environment is ready."""
    NIKKEI_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "playwright_installed": False,
        "chromium_installed": False,
        "profile_path": str(NIKKEI_PROFILE_DIR),
        "profile_exists": NIKKEI_PROFILE_DIR.exists(),
        "install_playwright_command": INSTALL_PLAYWRIGHT_CMD,
        "install_chromium_command": INSTALL_CHROMIUM_CMD,
        "warning": "",
    }

    sync_playwright = _load_sync_playwright()
    if not sync_playwright:
        result["warning"] = "Playwright 未安装。请先安装 playwright。"
        return result

    result["playwright_installed"] = True
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        result["chromium_installed"] = True
    except Exception as exc:
        result["warning"] = f"Chromium 未安装或无法启动：{exc}"
    return result


def nikkei_login() -> dict[str, Any]:
    """Open a visible dedicated Playwright profile and let the user log in manually."""
    NIKKEI_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    sync_playwright = _load_sync_playwright()
    if not sync_playwright:
        return {
            "ok": False,
            "profile_path": str(NIKKEI_PROFILE_DIR),
            "warning": f"Playwright 未安装。请运行 `{INSTALL_PLAYWRIGHT_CMD}` 和 `{INSTALL_CHROMIUM_CMD}`。",
        }

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch_persistent_context(
                user_data_dir=str(NIKKEI_PROFILE_DIR),
                headless=False,
                viewport={"width": 1280, "height": 900},
            )
            page = browser.pages[0] if browser.pages else browser.new_page()
            ok, warning = _goto_with_retries(page, NIKKEI_HOME, attempts=4)
            if not ok:
                raise RuntimeError(warning)
            print("已打开日经首页。请在浏览器中手动登录你的日经账号。")
            print("登录完成后，回到终端按 Enter。")
            print(f"登录状态只保存在专用 profile：{NIKKEI_PROFILE_DIR}")
            input()
            result = _page_status(page)
            result["ok"] = True
            result["profile_path"] = str(NIKKEI_PROFILE_DIR)
            result["login_status"] = result.get("logged_in", "unknown")
            browser.close()
            return result
    except Exception as exc:
        return {
            "ok": False,
            "profile_path": str(NIKKEI_PROFILE_DIR),
            "warning": f"无法打开日经登录浏览器：{exc}",
        }


def test_nikkei_login() -> dict[str, Any]:
    """Check login status using only the dedicated Nikkei Playwright profile."""
    sync_playwright = _load_sync_playwright()
    if not sync_playwright:
        return {
            "logged_in": "unknown",
            "current_url": "",
            "page_title": "",
            "visible_account_hint": "",
            "profile_path": str(NIKKEI_PROFILE_DIR),
            "warning": f"Playwright 未安装。请运行 `{INSTALL_PLAYWRIGHT_CMD}` 和 `{INSTALL_CHROMIUM_CMD}`。",
        }
    if not NIKKEI_PROFILE_DIR.exists():
        return {
            "logged_in": "unknown",
            "current_url": "",
            "page_title": "",
            "visible_account_hint": "",
            "profile_path": str(NIKKEI_PROFILE_DIR),
            "warning": f"日经专用 profile 不存在，请先运行 nikkei-login：{NIKKEI_PROFILE_DIR}",
        }

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch_persistent_context(
                user_data_dir=str(NIKKEI_PROFILE_DIR),
                headless=True,
                viewport={"width": 1280, "height": 900},
            )
            page = browser.pages[0] if browser.pages else browser.new_page()
            ok, warning = _goto_with_retries(page, NIKKEI_HOME)
            if not ok:
                raise RuntimeError(warning)
            result = _page_status(page)
            result["profile_path"] = str(NIKKEI_PROFILE_DIR)
            browser.close()
            return result
    except Exception as exc:
        return {
            "logged_in": "unknown",
            "current_url": "",
            "page_title": "",
            "visible_account_hint": "",
            "profile_path": str(NIKKEI_PROFILE_DIR),
            "warning": f"无法使用日经专用 profile 打开浏览器：{exc}",
        }


def test_nikkei_collect(
    run_date: str, max_articles: int = 5
) -> tuple[Path, Path, list[dict[str, Any]]]:
    return nikkei_collect(run_date, max_articles=max_articles, test_mode=True)


def nikkei_collect(
    run_date: str,
    max_articles: int = 10,
    test_mode: bool = False,
    stop_event: Any | None = None,
    progress_callback: Any | None = None,
) -> tuple[Path, Path, list[dict[str, Any]]]:
    """Conservatively collect Nikkei candidate links/articles through the dedicated profile."""
    status = test_nikkei_login()
    sync_playwright = _load_sync_playwright()
    if not sync_playwright or _nikkei_status_is_hard_blocker(status):
        rows = [_warning_row(status)]
        return _write_nikkei_outputs(run_date, rows, test_mode)

    effective_max_articles = _effective_nikkei_limit(max_articles, run_date, test_mode)
    rows: list[dict[str, Any]] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch_persistent_context(
                user_data_dir=str(NIKKEI_PROFILE_DIR),
                headless=True,
                viewport={"width": 1280, "height": 900},
            )
            page = browser.pages[0] if browser.pages else browser.new_page()
            links = _discover_nikkei_links(
                page,
                max_articles=_nikkei_scan_limit(effective_max_articles),
                stop_event=stop_event,
            )
            if stop_event is not None and stop_event.is_set():
                browser.close()
                return _write_nikkei_outputs(run_date, rows, test_mode)
            links = _rank_nikkei_links_for_collection(
                links, limit=_nikkei_collect_limit(effective_max_articles)
            )
            if not links:
                rows.append(
                    _warning_row(
                        {
                            **status,
                            "warning": "未发现日经候选链接。可能是页面结构变化、未登录或网络访问受限。",
                        }
                    )
                )
            for index, link in enumerate(links, start=1):
                if stop_event is not None and stop_event.is_set():
                    logger.info("Nikkei collection stopped by user request.")
                    break
                if progress_callback is not None:
                    progress_callback(index, len(links), link)
                collected = _collect_one_article(
                    page, link, status, run_date, stop_event=stop_event
                )
                if collected:
                    rows.append(collected)
                if stop_event is not None and stop_event.is_set():
                    break
                page.wait_for_timeout(5000)
            browser.close()
            if not rows and links:
                status_warning = str(status.get("warning", "")).strip()
                warning = (
                    "发现了日经候选链接，但没有成功读取到符合条件的会员正文。"
                    "可能是网络连接被重置、登录态失效、会员正文不可读取，"
                    "或文章不在当天收集时间窗口内。"
                )
                if status_warning:
                    warning = f"{warning} 登录检查提示：{status_warning}"
                rows.append(
                    _warning_row(
                        {
                            **status,
                            "warning": warning,
                        }
                    )
                )
    except Exception as exc:
        logger.info("Nikkei collect failed without article body logging: %s", exc)
        rows.append(_warning_row({**status, "warning": f"日经收集失败：{exc}"}))

    rows = sorted(rows, key=lambda row: float(row.get("score") or 0), reverse=True)[
        :effective_max_articles
    ]
    return _write_nikkei_outputs(run_date, rows, test_mode)


def _effective_nikkei_limit(max_articles: int, run_date: str, test_mode: bool) -> int:
    if test_mode:
        return max(1, int(max_articles or 1))
    multiplier = collection_window_multiplier(run_date)
    return max(1, min(120, int(max_articles or 1) * multiplier))


def _nikkei_scan_limit(effective_max_articles: int) -> int:
    return max(effective_max_articles, min(240, effective_max_articles * 4))


def _nikkei_collect_limit(effective_max_articles: int) -> int:
    return max(1, min(60, effective_max_articles * 2))


def _rank_nikkei_links_for_collection(
    links: list[dict[str, str]], limit: int
) -> list[dict[str, str]]:
    keywords = load_keywords()
    samples = load_reference_samples()
    ranked: list[tuple[float, int, dict[str, str]]] = []
    for index, link in enumerate(links):
        title = clean_text(link.get("title_original", ""))
        source_section = clean_text(link.get("source_section", ""))
        article = {
            "title_original": title,
            "raw_text": title,
            "source": "Nikkei",
            "source_section": source_section,
            "country_region": "Japan",
            "language": "ja",
            "source_priority": 5,
            "source_type": "login_browser",
            "source_domain": "www.nikkei.com",
        }
        scored = score_candidate(article, keywords, samples)
        link_priority = _nikkei_link_priority(title, source_section)
        ranked.append((float(scored.get("score") or 0) + link_priority, -index, link))
    ranked.sort(reverse=True)
    return [item[2] for item in ranked[:limit]]


def _nikkei_link_priority(title: str, source_section: str) -> float:
    combined = f"{title} {source_section}".lower()
    priority = 0.0
    if any(term.lower() in combined for term in HIGH_VALUE_NIKKEI_TITLE_TERMS):
        priority += 80.0
    if any(section in source_section for section in ["サイエンス", "AI", "半導体"]):
        priority += 35.0
    elif "Tech" in source_section:
        priority += 20.0
    if any(term in combined for term in ["公募", "研究支援", "文部科学省", "文科省"]):
        priority += 30.0
    return priority


def fetch_nikkei_full_text(url: str, stop_event: Any | None = None) -> dict[str, Any]:
    """Fetch the full visible body for one selected Nikkei article.

    This uses only the dedicated Nikkei Playwright profile. It never logs the
    article body. If the body cannot be read, the caller gets a warning.
    """
    status = test_nikkei_login()
    sync_playwright = _load_sync_playwright()
    if not sync_playwright or _nikkei_status_is_hard_blocker(status):
        return {
            "raw_text": "",
            "title_original": "",
            "published_date": "",
            "extraction_warning": status.get("warning", "日经登录或浏览器环境不可用"),
        }

    try:
        if stop_event is not None and stop_event.is_set():
            return {
                "raw_text": "",
                "title_original": "",
                "published_date": "",
                "extraction_warning": "selected_fulltext_stopped",
            }
        with sync_playwright() as p:
            browser = p.chromium.launch_persistent_context(
                user_data_dir=str(NIKKEI_PROFILE_DIR),
                headless=True,
                viewport={"width": 1280, "height": 900},
            )
            page = browser.pages[0] if browser.pages else browser.new_page()
            ok, warning = _goto_with_retries(
                page, url, wait_after_ms=2500, stop_event=stop_event
            )
            if stop_event is not None and stop_event.is_set():
                browser.close()
                return {
                    "raw_text": "",
                    "title_original": "",
                    "published_date": "",
                    "extraction_warning": "selected_fulltext_stopped",
                }
            if not ok:
                browser.close()
                return {
                    "raw_text": "",
                    "title_original": "",
                    "published_date": "",
                    "extraction_warning": warning,
                }
            body = clean_text(page.locator("body").inner_text(timeout=10000))
            raw_text = _extract_article_text(page, body)
            result = {
                "raw_text": raw_text,
                "title_original": _safe_locator_text(page, "h1") or clean_text(page.title()),
                "published_date": _extract_date(body),
                "extraction_warning": "",
            }
            browser.close()
            if not _has_usable_article_body(raw_text, body):
                result["extraction_warning"] = "selected_fulltext_unavailable"
            return result
    except Exception as exc:
        logger.info("Nikkei selected full text fetch failed without body logging: %s", exc)
        return {
            "raw_text": "",
            "title_original": "",
            "published_date": "",
            "extraction_warning": f"selected_fulltext_failed: {exc}",
        }


def _discover_nikkei_links(
    page, max_articles: int, stop_event: Any | None = None
) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    visited_sections: set[str] = set()
    section_queue = list(_nikkei_discovery_sections())

    while section_queue and len(links) < max_articles:
        if stop_event is not None and stop_event.is_set():
            break
        section = section_queue.pop(0)
        discovery_url = section["url"]
        source_section = section["section"]
        if discovery_url in visited_sections:
            continue
        visited_sections.add(discovery_url)
        try:
            ok, warning = _goto_with_retries(
                page,
                discovery_url,
                wait_after_ms=5000,
                label=f"日经发现页 {discovery_url}",
                stop_event=stop_event,
            )
            if stop_event is not None and stop_event.is_set():
                break
            if not ok:
                logger.info("Nikkei discovery page skipped after retries: %s", warning)
                continue
            if not _nikkei_discovery_page_is_relevant(
                source_section, page.url, clean_text(page.title())
            ):
                logger.info(
                    "Nikkei discovery page skipped as stale/off-topic: %s | %s | %s",
                    source_section,
                    page.url,
                    clean_text(page.title()),
                )
                continue
            for section_url in _discover_section_urls_from_page(page):
                if stop_event is not None and stop_event.is_set():
                    break
                if (
                    section_url not in visited_sections
                    and all(item["url"] != section_url for item in section_queue)
                    and len(visited_sections) + len(section_queue) < MAX_NIKKEI_SECTION_PAGES
                ):
                    section_queue.append(
                        {
                            "url": section_url,
                            "section": _nikkei_section_label(source_section, section_url),
                        }
                    )
            per_section_limit = max(8, min(30, max_articles // 6))
            for link in _discover_links_from_page(
                page, limit=per_section_limit, source_section=source_section
            ):
                if stop_event is not None and stop_event.is_set():
                    break
                if link["url"] in seen:
                    continue
                seen.add(link["url"])
                links.append(link)
                if len(links) >= max_articles:
                    return links
        except Exception as exc:
            logger.info("Nikkei discovery page failed without body logging: %s", exc)
            continue
    return links


def _nikkei_discovery_sections() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for section, url in NIKKEI_PRIORITY_SECTIONS:
        rows.append({"url": url, "section": section})
    try:
        nikkei = next((source for source in load_sources() if source.name == "Nikkei"), None)
        if nikkei:
            for url in nikkei.discovery_urls:
                rows.append({"url": url, "section": _nikkei_section_label("Nikkei", url)})
    except Exception:
        pass
    for url in NIKKEI_DISCOVERY_URLS:
        rows.append({"url": url, "section": _nikkei_section_label("Nikkei", url)})
    output = []
    seen = set()
    for row in rows:
        url = row.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        output.append(row)
    return output


def _nikkei_section_label(default: str, url: str) -> str:
    lower = url.lower()
    if "science" in lower:
        return "Tech/サイエンス"
    if "technology" in lower:
        return "Tech"
    if "business" in lower:
        return "ビジネス"
    if "politics" in lower:
        return "政治/政策"
    if "economy" in lower:
        return "経済"
    return default


def _nikkei_discovery_page_is_relevant(
    _source_section: str, final_url: str, page_title: str
) -> bool:
    """Guard against stale Nikkei topic IDs being reused for unrelated pages."""
    parsed = urlparse(final_url)
    topic_like = parsed.path.startswith("/topics/") or parsed.path.startswith(
        "/stories/topic_story"
    )
    if not topic_like:
        return True

    title_without_site_name = re.sub(r"[-｜|]\s*日本経済新聞.*$", "", page_title).replace(
        "日本経済新聞", ""
    )
    combined = f"{final_url} {title_without_site_name}".lower()
    allowed_terms = [
        *NIKKEI_SECTION_HINTS,
        *HIGH_VALUE_NIKKEI_TITLE_TERMS,
        "研究",
        "科学",
        "技術",
        "政策",
        "産業",
    ]
    return any(term.lower() in combined for term in allowed_terms)


def _discover_section_urls_from_page(page) -> list[str]:
    try:
        anchors = page.locator("a[href]").evaluate_all(
            """els => els.map(a => ({href: a.href, text: (a.innerText || a.title || '').trim()}))"""
        )
    except Exception:
        return []

    rows = []
    seen = set()
    for anchor in anchors:
        title = clean_text(anchor.get("text", ""))
        url = urljoin(NIKKEI_HOME, anchor.get("href", ""))
        parsed = urlparse(url)
        if parsed.netloc != "www.nikkei.com":
            continue
        if "/article/" in parsed.path:
            continue
        if url in seen:
            continue
        if not _is_priority_section(title, url):
            continue
        seen.add(url)
        rows.append(url)
    return rows[:8]


def _collect_one_article(
    page,
    link: dict[str, str],
    status: dict[str, Any],
    run_date: str,
    stop_event: Any | None = None,
) -> dict[str, Any] | None:
    warning_parts = []
    raw_text = ""
    title = clean_text(link.get("title_original", ""))
    published_date = ""

    try:
        ok, warning = _goto_with_retries(
            page,
            link["url"],
            wait_after_ms=1500,
            label=f"日经文章 {link['url']}",
            stop_event=stop_event,
        )
        if stop_event is not None and stop_event.is_set():
            return None
        if not ok:
            warning_parts.append(warning)
            return None
        page_title = clean_text(page.title())
        h1_title = _safe_locator_text(page, "h1")
        title = h1_title or page_title or title
        body = clean_text(page.locator("body").inner_text(timeout=10000))
        raw_text = _extract_article_text(page, body)
        published_date = _extract_date(body)
        if not _has_usable_article_body(raw_text, body):
            return None
        if status.get("logged_in") is False:
            warning_parts.append("登录状态可能无效")
        if len(raw_text) < 200:
            warning_parts.append("正文过短，仅保存标题和可见内容")
        elif _looks_paywalled_or_partial(body) and status.get("logged_in") is not True:
            warning_parts.append("会员正文可能不可读取或只显示部分内容")
    except Exception as exc:
        warning_parts.append(f"文章访问失败：{exc}")

    keep, window_status = is_in_collection_window(published_date, run_date)
    if not keep:
        return None
    warning = append_window_warning("；".join(warning_parts), window_status)

    row = {
        "title_original": title,
        "published_date": published_date,
        "url": link["url"],
        "source": "Nikkei",
        "source_section": link.get("source_section", ""),
        "raw_text": raw_text,
        "raw_text_preview": raw_text[:500],
        "extraction_warning": warning,
        "logged_in_status": status.get("logged_in", "unknown"),
        "source_domain": urlparse(link["url"]).netloc,
        "country_region": "Japan",
        "language": "ja",
        "source_priority": 5,
        "source_type": "login_browser",
    }
    if is_excluded_topic(row):
        return None
    scored = score_candidate(row, load_keywords(), load_reference_samples())
    row.update(
        {
            "score": scored.get("score", 0),
            "recommended_reason": scored.get("recommended_reason", ""),
            "matched_keywords": scored.get("matched_keywords", ""),
        }
    )
    return row


def _has_usable_article_body(raw_text: str, page_body: str) -> bool:
    if len(clean_text(raw_text)) < MIN_NIKKEI_BODY_CHARS:
        return False
    if _looks_like_navigation(raw_text[:900]):
        return False
    if _looks_paywalled_or_partial(page_body) and len(clean_text(raw_text)) < 1000:
        return False
    return True


def _goto_with_retries(
    page,
    url: str,
    *,
    wait_until: str = "domcontentloaded",
    timeout_ms: int = NIKKEI_NAV_TIMEOUT_MS,
    attempts: int = NIKKEI_NAV_RETRY_ATTEMPTS,
    retry_wait_ms: int = NIKKEI_NAV_RETRY_WAIT_MS,
    wait_after_ms: int = 0,
    label: str = "",
    stop_event: Any | None = None,
) -> tuple[bool, str]:
    target = label or url
    last_error = ""
    total_attempts = max(1, int(attempts or 1))
    for attempt in range(1, total_attempts + 1):
        if stop_event is not None and stop_event.is_set():
            return False, f"{target} 已按用户请求停止。"
        try:
            page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            if stop_event is not None and stop_event.is_set():
                return False, f"{target} 已按用户请求停止。"
            if wait_after_ms > 0:
                if _playwright_wait_stopped(page, wait_after_ms, stop_event):
                    return False, f"{target} 已按用户请求停止。"
            if attempt > 1:
                logger.info(
                    "Nikkei page opened after retry %s/%s: %s",
                    attempt,
                    total_attempts,
                    target,
                )
            return True, ""
        except Exception as exc:
            last_error = str(exc)
            logger.info(
                "Nikkei page open failed %s/%s for %s without body logging: %s",
                attempt,
                total_attempts,
                target,
                exc,
            )
            if attempt >= total_attempts:
                break
            if stop_event is not None and stop_event.is_set():
                return False, f"{target} 已按用户请求停止。"
            try:
                if _playwright_wait_stopped(page, retry_wait_ms * attempt, stop_event):
                    return False, f"{target} 已按用户请求停止。"
            except Exception:
                pass
    return False, f"{target} 访问失败，已重试 {total_attempts} 次：{last_error}"


def _playwright_wait_stopped(page, wait_ms: int, stop_event: Any | None = None) -> bool:
    if wait_ms <= 0:
        return bool(stop_event is not None and stop_event.is_set())
    if stop_event is None:
        page.wait_for_timeout(wait_ms)
        return False
    remaining = int(wait_ms)
    while remaining > 0:
        if stop_event.is_set():
            return True
        chunk = min(250, remaining)
        page.wait_for_timeout(chunk)
        remaining -= chunk
    return stop_event.is_set()


def _nikkei_status_is_hard_blocker(status: dict[str, Any]) -> bool:
    warning = str(status.get("warning", ""))
    hard_markers = [
        "Playwright 未安装",
        "日经专用 profile 不存在",
        "Chromium 未安装",
    ]
    return any(marker in warning for marker in hard_markers)


def _discover_links_from_page(
    page, limit: int = 5, source_section: str = ""
) -> list[dict[str, str]]:
    anchors = page.locator("a[href]").evaluate_all(
        """els => els.map(a => ({href: a.href, text: (a.innerText || a.title || '').trim()}))"""
    )
    rows = []
    seen = set()
    for anchor in anchors:
        url = urljoin(NIKKEI_HOME, anchor.get("href", ""))
        title = clean_text(anchor.get("text", ""))
        parsed = urlparse(url)
        if parsed.netloc != "www.nikkei.com":
            continue
        if "/article/" not in parsed.path:
            continue
        if _is_low_value_nikkei_link(title, url):
            continue
        if url in seen:
            continue
        seen.add(url)
        rows.append(
            {
                "url": url,
                "title_original": title,
                "source_section": source_section,
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _is_priority_section(title: str, url: str) -> bool:
    combined = f"{title} {url}".lower()
    if any(hint.lower() in combined for hint in NIKKEI_NEGATIVE_SECTION_HINTS):
        return False
    return any(hint.lower() in combined for hint in NIKKEI_SECTION_HINTS)


def _is_low_value_nikkei_link(title: str, url: str) -> bool:
    combined = f"{title} {url}".lower()
    low_value_terms = [
        "グルメ",
        "スポーツ",
        "エンタメ",
        "おくやみ",
        "カリスマ経営",
        "ライフ",
        "トラベル",
        "たこ焼き",
        "冷凍食品",
    ]
    return any(term.lower() in combined for term in low_value_terms)


def _page_status(page) -> dict[str, Any]:
    title = clean_text(page.title())
    body = clean_text(page.locator("body").inner_text(timeout=10000))
    current_url = page.url
    logged_in: bool | str = "unknown"
    warning = ""
    visible_account_hint = ""

    if "ログアウト" in body:
        logged_in = True
        visible_account_hint = "ログアウト link detected"
    elif "マイページ" in body or "Myニュース" in body or "電子版" in body:
        logged_in = True
        visible_account_hint = "member navigation text detected"
    elif "ログイン" in body or "会員登録" in body:
        logged_in = False
        visible_account_hint = "login/register text detected"

    if logged_in == "unknown":
        warning = "无法可靠判断登录状态，请确认日经页面右上角是否显示会员入口或账户菜单。"
    return {
        "logged_in": logged_in,
        "current_url": current_url,
        "page_title": title,
        "visible_account_hint": visible_account_hint,
        "warning": warning,
    }


def _write_nikkei_outputs(
    run_date: str, rows: list[dict[str, Any]], test_mode: bool
) -> tuple[Path, Path, list[dict[str, Any]]]:
    jsonl_name = "nikkei_test" if test_mode else "nikkei_collect"
    jsonl_path = DATA_ROOT / "processed" / f"{jsonl_name}_{compact_date(run_date)}.jsonl"
    excel_name = (
        f"nikkei_test_candidates_{compact_date(run_date)}.xlsx"
        if test_mode
        else f"nikkei_candidates_{compact_date(run_date)}.xlsx"
    )
    excel_path = DATA_ROOT / "output" / excel_name
    safe_rows = [_safe_output_row(row) for row in rows]
    write_jsonl(jsonl_path, safe_rows)
    excel_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(safe_rows, columns=NIKKEI_COLUMNS).to_excel(excel_path, index=False)
    return jsonl_path, excel_path, safe_rows


def _safe_output_row(row: dict[str, Any]) -> dict[str, Any]:
    output = {column: row.get(column, "") for column in NIKKEI_COLUMNS}
    if len(str(output.get("raw_text_preview", ""))) > 500:
        output["raw_text_preview"] = str(output["raw_text_preview"])[:500]
    return output


def _warning_row(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "title_original": "",
        "published_date": "",
        "url": NIKKEI_HOME,
        "source": "Nikkei",
        "source_section": "",
        "raw_text_preview": "",
        "extraction_warning": status.get("warning", "日经登录或浏览器环境不可用"),
        "logged_in_status": status.get("logged_in", "unknown"),
        "score": 0,
        "recommended_reason": "",
        "matched_keywords": "",
    }


def _safe_locator_text(page, selector: str) -> str:
    try:
        locator = page.locator(selector)
        if locator.count() == 0:
            return ""
        return clean_text(locator.first.inner_text(timeout=3000))
    except Exception:
        return ""


def _extract_article_text(page, body: str) -> str:
    """Extract visible article-like text while avoiding global navigation."""
    selector_candidates = [
        "article",
        "main article",
        "main",
        "[data-track-article-body]",
        "[class*='article']",
        "[class*='Article']",
        "[class*='body']",
        "[class*='Body']",
    ]
    for selector in selector_candidates:
        try:
            locator = page.locator(selector)
            if locator.count() == 0:
                continue
            text = clean_text(locator.first.inner_text(timeout=3000))
            if _article_text_score(text) >= 4:
                return _clean_article_text(text)
        except Exception:
            continue

    paragraphs = _paragraph_text(page)
    if paragraphs:
        return _clean_article_text(" ".join(paragraphs))
    return _clean_article_text(body)


def _paragraph_text(page) -> list[str]:
    try:
        paragraphs = page.locator("p").evaluate_all(
            """els => els.map(e => (e.innerText || '').trim()).filter(Boolean)"""
        )
    except Exception:
        return []
    output = []
    seen = set()
    for text in paragraphs:
        text = clean_text(text)
        if len(text) < 25:
            continue
        if _looks_like_navigation(text):
            continue
        if text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def _clean_article_text(text: str) -> str:
    text = clean_text(text)
    markers = [
        "メインコンテンツへスキップ",
        "トップ 速報 ビジネス マーケット",
        "この記事は会員限定記事です",
        "この記事は会員限定です",
        "登録すると続きをお読みいただけます",
    ]
    for marker in markers:
        if marker in text:
            parts = text.split(marker)
            text = parts[-1] if len(parts[-1]) > 200 else text
    return text


def _article_text_score(text: str) -> int:
    if not text:
        return 0
    score = 0
    if len(text) >= 300:
        score += 2
    if re.search(r"。|です|ます|した|する|円|ドル|年度|年\d{1,2}月", text):
        score += 2
    if not _looks_like_navigation(text[:500]):
        score += 1
    return score


def _looks_like_navigation(text: str) -> bool:
    nav_terms = [
        "メインコンテンツへスキップ",
        "検索 ファミリー会員",
        "トップ 速報 ビジネス マーケット",
        "Myニュース 日経会社情報",
        "もっと見る",
    ]
    return any(term in text for term in nav_terms)


def _extract_date(text: str) -> str:
    match = re.search(
        r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日[^\d]{0,8}(\d{1,2}):(\d{2})",
        text,
    )
    if match:
        year, month, day, hour, minute = match.groups()
        return (
            f"{int(year):04d}-{int(month):02d}-{int(day):02d} "
            f"{int(hour):02d}:{int(minute):02d}"
        )
    match = re.search(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日", text)
    if match:
        year, month, day = match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    match = re.search(r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})", text)
    if match:
        year, month, day = match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    return ""


def _looks_paywalled_or_partial(text: str) -> bool:
    hints = [
        "この記事は会員限定",
        "続きをお読みいただくには",
        "ログインすると",
        "電子版に登録",
    ]
    return any(hint in text for hint in hints)


def _load_sync_playwright():
    try:
        from playwright.sync_api import sync_playwright

        return sync_playwright
    except Exception:
        logger.info("Playwright is not available for Nikkei browser automation.")
        return None
