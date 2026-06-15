from __future__ import annotations

import ipaddress
import re
from collections import Counter
from typing import Any
from urllib.parse import urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

from src.collectors.discovery import (
    _is_relevant_for_source,
    _select_anchors,
    _title_from_anchor,
)
from src.config_loader import NewsSource
from src.parsers.article_extractor import ArticleExtractor, DEFAULT_HEADERS


def diagnose_source(
    source: NewsSource | dict[str, Any],
    *,
    sample_size: int = 2,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    source = source if isinstance(source, NewsSource) else _source_from_mapping(source)
    report: dict[str, Any] = {
        "source_name": source.name,
        "status": "needs_rules",
        "reachable": False,
        "candidate_count": 0,
        "sampled_articles": [],
        "detected_feeds": [],
        "suggested_patch": {},
        "recommendations": [],
    }
    if source.requires_login or source.source_type == "login_browser":
        report["status"] = "needs_adapter"
        report["recommendations"].append(
            "该网站需要登录；公开版只能检查入口，正式采集需要合法登录会话和专用适配器。"
        )
        return report

    discovery_urls = source.discovery_urls or [source.section_url or source.base_url]
    candidates: list[dict[str, str]] = []
    errors: list[str] = []
    for discovery_url in discovery_urls[:4]:
        validation_error = public_url_validation_error(discovery_url)
        if validation_error:
            errors.append(f"{discovery_url}: {validation_error}")
            continue
        try:
            response = requests.get(
                discovery_url,
                headers=DEFAULT_HEADERS,
                timeout=timeout_seconds,
                allow_redirects=True,
            )
            response.raise_for_status()
            redirect_error = public_url_validation_error(response.url)
            if redirect_error:
                errors.append(f"{discovery_url}: 重定向目标不允许访问")
                continue
            report["reachable"] = True
        except Exception as exc:
            errors.append(f"{discovery_url}: {exc}")
            continue

        content_type = str(response.headers.get("content-type") or "").lower()
        if source.source_type == "rss" or "xml" in content_type:
            candidates.extend(_feed_candidates(response.content, source))
            continue
        html = response.text
        report["detected_feeds"].extend(_detected_feed_urls(html, response.url))
        candidates.extend(_html_candidates(html, response.url, source))

    candidates = _dedupe_candidates(candidates)
    report["candidate_count"] = len(candidates)
    report["candidate_examples"] = candidates[:8]
    report["detected_feeds"] = list(dict.fromkeys(report["detected_feeds"]))[:8]
    if errors:
        report["errors"] = errors[:5]

    extractor = ArticleExtractor(timeout_seconds=timeout_seconds)
    for candidate in candidates[: max(0, sample_size)]:
        sample = {"url": candidate["url"], "title_hint": candidate["title"]}
        try:
            extracted = extractor.extract_from_url(candidate["url"])
            sample.update(
                {
                    "title": extracted.title,
                    "published_date": extracted.published_date,
                    "text_chars": len(extracted.text),
                    "passed": bool(extracted.title and len(extracted.text) >= 250),
                }
            )
        except Exception as exc:
            sample.update({"passed": False, "error": str(exc)})
        report["sampled_articles"].append(sample)

    passed_samples = sum(
        bool(sample.get("passed")) for sample in report["sampled_articles"]
    )
    if report["reachable"] and candidates and passed_samples:
        report["status"] = "ready"
        report["recommendations"].append("入口发现和正文抽取均通过，可以添加并启用。")
    elif report["reachable"] and candidates:
        report["status"] = "warning"
        report["recommendations"].append(
            "已发现候选链接，但样本正文抽取不稳定；建议先保存为禁用配置并检查页面结构。"
        )
    elif report["reachable"]:
        report["status"] = "needs_rules"
        report["recommendations"].append(
            "入口可以访问，但没有识别出文章链接；请换成更具体的栏目/RSS，或配置链接选择器与 URL 规则。"
        )
    else:
        report["status"] = "unreachable"
        report["recommendations"].append(
            "入口无法访问；请检查 URL、网络、robots/服务条款或站点访问限制。"
        )

    suggested_pattern = _suggest_include_pattern(candidates)
    if suggested_pattern:
        report["suggested_patch"]["include_url_patterns"] = [suggested_pattern]
    if report["detected_feeds"] and source.source_type != "rss":
        report["recommendations"].append(
            f"检测到 RSS/Atom，可另行诊断并优先使用：{report['detected_feeds'][0]}"
        )
    return report


def public_url_validation_error(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "只支持完整的 http/https URL"
    hostname = parsed.hostname.lower()
    if hostname == "localhost" or hostname.endswith(".local"):
        return "不允许诊断本机地址"
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return ""
    if not address.is_global:
        return "不允许诊断私有或本机 IP"
    return ""


def _source_from_mapping(item: dict[str, Any]) -> NewsSource:
    discovery_urls = [
        str(value).strip()
        for value in item.get("discovery_urls") or []
        if str(value).strip()
    ]
    base_url = str(item.get("base_url") or item.get("section_url") or "").strip()
    section_url = str(item.get("section_url") or "").strip()
    if not section_url:
        section_url = (discovery_urls or [base_url])[0]
    return NewsSource(
        name=str(item.get("name") or urlparse(base_url).netloc or "New source"),
        country_region=str(item.get("country_region") or "Global"),
        language=str(item.get("language") or "en"),
        section_url=section_url,
        source_type=str(item.get("source_type") or "html").lower(),
        requires_login=bool(item.get("requires_login", False)),
        priority=int(item.get("priority") or 3),
        base_url=base_url,
        tags=[str(value) for value in item.get("tags") or []],
        discovery_urls=discovery_urls or [base_url],
        link_selectors=[str(value) for value in item.get("link_selectors") or []],
        include_url_patterns=[
            str(value) for value in item.get("include_url_patterns") or []
        ],
        exclude_url_patterns=[
            str(value) for value in item.get("exclude_url_patterns") or []
        ],
        rate_limit_seconds=float(item.get("rate_limit_seconds") or 1.5),
        max_articles_per_run=int(item.get("max_articles_per_run") or 20),
        enabled=bool(item.get("enabled", False)),
    )


def _feed_candidates(content: bytes, source: NewsSource) -> list[dict[str, str]]:
    parsed = feedparser.parse(content)
    return [
        {
            "url": str(entry.get("link") or "").strip(),
            "title": str(entry.get("title") or "").strip(),
        }
        for entry in parsed.entries[:50]
        if str(entry.get("link") or "").strip()
        and _is_relevant_for_source(
            source,
            str(entry.get("link") or "").strip(),
            str(entry.get("title") or "").strip(),
        )
    ]


def _html_candidates(html: str, base_url: str, source: NewsSource) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    base_domain = urlparse(source.base_url or base_url).netloc
    selectors = source.link_selectors or ["article a[href]", "main a[href]", "a[href]"]
    rows = []
    for anchor in _select_anchors(soup, selectors):
        url = urljoin(base_url, str(anchor.get("href") or ""))
        parsed = urlparse(url)
        title = _title_from_anchor(anchor)
        if parsed.scheme not in {"http", "https"}:
            continue
        if base_domain and parsed.netloc != base_domain:
            continue
        if not _is_relevant_for_source(source, url, title):
            continue
        rows.append({"url": url, "title": title})
        if len(rows) >= 60:
            break
    return rows


def _detected_feed_urls(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for node in soup.select("link[rel='alternate'][href]"):
        feed_type = str(node.get("type") or "").lower()
        if "rss" in feed_type or "atom" in feed_type or "xml" in feed_type:
            urls.append(urljoin(base_url, str(node.get("href") or "")))
    return urls


def _dedupe_candidates(candidates: list[dict[str, str]]) -> list[dict[str, str]]:
    output = []
    seen = set()
    for candidate in candidates:
        url = candidate.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        output.append(candidate)
    return output


def _suggest_include_pattern(candidates: list[dict[str, str]]) -> str:
    if len(candidates) < 3:
        return ""
    first_segments = []
    domain_counts: Counter[str] = Counter()
    for candidate in candidates[:30]:
        parsed = urlparse(candidate["url"])
        domain_counts[parsed.netloc] += 1
        segments = [segment for segment in parsed.path.split("/") if segment]
        if segments:
            first_segments.append(segments[0])
    if not first_segments or not domain_counts:
        return ""
    segment, count = Counter(first_segments).most_common(1)[0]
    if count < max(3, len(first_segments) // 2):
        return ""
    domain = domain_counts.most_common(1)[0][0]
    return rf"^https?://{re.escape(domain)}/{re.escape(segment)}/"
