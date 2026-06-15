from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from src.llm.openai_client import OpenAIClient


SYSTEM_PROMPT = """
你是 AutoHeadlines 的配置助手。AutoHeadlines 用于收集、筛选、总结和统计科技新闻。
请用用户使用的语言简洁回答问题，并在适合时推荐可作为信息源的网站。

你没有实时联网检索能力，因此不能声称 URL 已经验证，也不能保证网站仍然可访问。
推荐来源时优先选择官方机构、研究机构、稳定 RSS、科技媒体的明确栏目页，避免只给首页。
需要登录、付费墙、JavaScript 渲染或反爬保护的网站必须明确指出。

你必须只返回一个 JSON 对象：
{
  "answer": "给用户的回答",
  "recommended_sources": [
    {
      "name": "唯一、简短的网站名称",
      "base_url": "https://...",
      "country_region": "US/EU/Global/...",
      "language": "en/ja/ko/zh/mixed",
      "source_type": "rss/html/official/media/login_browser",
      "priority": 1,
      "tags": ["AI", "research"],
      "discovery_urls": ["https://.../明确栏目或RSS"],
      "include_url_patterns": [],
      "exclude_url_patterns": [],
      "reason": "推荐理由和注意事项"
    }
  ]
}
如果用户没有要求推荐网站，recommended_sources 返回空数组。不要输出 Markdown 或思考过程。
""".strip()

ALLOWED_SOURCE_TYPES = {"rss", "html", "official", "media", "login_browser"}


def ask_configuration_assistant(
    messages: list[dict[str, str]],
    existing_sources: list[str] | None = None,
    client: OpenAIClient | None = None,
) -> dict[str, Any]:
    client = client or OpenAIClient()
    result = client.generate_json(
        SYSTEM_PROMPT,
        {
            "conversation": [
                {
                    "role": str(message.get("role", "user")),
                    "content": str(message.get("content", ""))[:4000],
                }
                for message in messages[-12:]
            ],
            "existing_source_names": existing_sources or [],
        },
    )
    answer = str(result.get("answer") or "").strip()
    recommendations = [
        source
        for item in result.get("recommended_sources") or []
        if (source := normalize_source_suggestion(item)) is not None
    ]
    if not answer:
        answer = "模型没有返回可显示的回答，请换一种说法再试一次。"
    return {"answer": answer, "recommended_sources": recommendations}


def normalize_source_suggestion(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or "").strip()
    base_url = _valid_http_url(item.get("base_url"))
    discovery_urls = [
        url
        for value in item.get("discovery_urls") or []
        if (url := _valid_http_url(value))
    ]
    if not base_url and discovery_urls:
        base_url = discovery_urls[0]
    if not name or not base_url:
        return None
    if not discovery_urls:
        discovery_urls = [base_url]
    source_type = str(item.get("source_type") or "html").strip().lower()
    if source_type not in ALLOWED_SOURCE_TYPES:
        source_type = "html"
    try:
        priority = max(1, min(5, int(item.get("priority") or 3)))
    except (TypeError, ValueError):
        priority = 3
    return {
        "name": name[:120],
        "base_url": base_url,
        "country_region": str(item.get("country_region") or "Global").strip()[:60],
        "language": str(item.get("language") or "en").strip()[:20],
        "section_url": discovery_urls[0],
        "source_type": source_type,
        "requires_login": source_type == "login_browser",
        "priority": priority,
        "enabled": False,
        "tags": _string_list(item.get("tags"), limit=12),
        "discovery_urls": discovery_urls[:8],
        "link_selectors": _string_list(item.get("link_selectors"), limit=8),
        "include_url_patterns": _string_list(
            item.get("include_url_patterns"), limit=12
        ),
        "exclude_url_patterns": _string_list(
            item.get("exclude_url_patterns"), limit=12
        ),
        "rate_limit_seconds": 5 if source_type == "login_browser" else 1.5,
        "max_articles_per_run": 20,
        "reason": str(item.get("reason") or "").strip()[:1000],
    }


def source_config_from_suggestion(
    suggestion: dict[str, Any], *, enabled: bool = False
) -> dict[str, Any]:
    source = normalize_source_suggestion(suggestion)
    if source is None:
        raise ValueError("The source suggestion is missing a valid name or public URL.")
    source["enabled"] = enabled
    source.pop("reason", None)
    return source


def _valid_http_url(value: Any) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return url


def _string_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:500] for item in value if str(item).strip()][:limit]
