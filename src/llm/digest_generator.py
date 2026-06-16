from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from src.config_loader import DATA_ROOT, load_profile
from src.llm.openai_client import OpenAIClient
from src.parsers.text_cleaner import clean_text
from src.utils.dates import compact_date
from src.utils.logger import get_logger


logger = get_logger(__name__)
FOREIGN_SCRIPT_RE = re.compile(r"[\u3040-\u30ff\uac00-\ud7af]")
CHINESE_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")
CHINESE_REPAIR_ATTEMPTS = 2
LOW_QUALITY_DIGEST_MARKERS = [
    "该消息来自已选候选新闻",
    "建议结合原文链接复核",
    "可作为当日科技要闻线索",
    "建议人工复核后补充摘要正文",
    "发布一项与",
    "相关科技动态",
]
CHINESE_REPAIR_PROMPT = """
你是中文科技要闻编辑。请重新生成一版严格合格的中文摘要。

强制要求：
1. title_cn 和 summary_cn 必须使用简体中文，可以保留 NEDO、AI、WSTS 等英文缩写；
2. 禁止出现日文平假名、片假名或韩文；
3. 不要直接复制原文标题或原文段落；
4. summary_cn 控制在约200字以内，遵循“谁—为了解决什么问题—做了什么—结果怎么样”；
5. 只输出 JSON，字段为 title_cn、summary_cn、keywords、type、soft_hard；
6. type 只能是：政策、技术、产业；
7. soft_hard 只能是：软科学、硬科学。
8. 标题和摘要必须包含原文中的具体事实，不得写“相关科技动态”“建议结合原文复核”等占位文字。
""".strip()
CHINESE_CLEANUP_PROMPT = """
你是中文科技编辑。清理给定标题和摘要中的所有日文平假名、片假名或韩文。

强制要求：
1. 能确定名称时翻译成简体中文或通用英文；
2. 不能确定名称时改写为“相关日本企业/机构”或“相关韩国企业/机构”，不要保留外文字符；
3. 保留已有具体事实，不得加入新事实，不得改成空泛占位摘要；
4. 只输出 JSON，字段为 title_cn、summary_cn、keywords、type、soft_hard；
5. type 只能是：政策、技术、产业；
6. soft_hard 只能是：软科学、硬科学。
""".strip()


def final_json_path(run_date: str) -> Path:
    return DATA_ROOT / "output" / f"final_digest_{compact_date(run_date)}.json"


def generate_digests(
    selected_candidates: list[dict[str, Any]],
    run_date: str,
    client: OpenAIClient | None = None,
    stop_event: Any | None = None,
    progress_callback: Any | None = None,
    quality_report: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    profile = load_profile()
    prompt = profile.prompt_file.read_text(encoding="utf-8")
    client = client or OpenAIClient()
    allow_demo_value = (
        os.getenv("XAUTOHEADLINES_ALLOW_DEMO_SUMMARIES")
        or os.getenv("AUTOHEADLINES_ALLOW_DEMO_SUMMARIES")
        or ""
    )
    allow_demo = allow_demo_value.lower() in {
        "1",
        "true",
        "yes",
    }
    if not client.is_configured and not allow_demo:
        raise RuntimeError(
            "No LLM provider is configured. Configure OpenAI or Ollama before "
            "generating a release-quality digest. To explicitly test formatting "
            "with demo summaries, set XAUTOHEADLINES_ALLOW_DEMO_SUMMARIES=true."
        )
    digests: list[dict[str, Any]] = []

    for index, candidate in enumerate(selected_candidates, start=1):
        if stop_event is not None and stop_event.is_set():
            logger.info("Digest generation stopped by user request.")
            break
        if progress_callback is not None:
            progress_callback(index, len(selected_candidates), candidate)
        source_text = _source_text(candidate)
        try:
            digest = _generate_one(candidate, prompt, client)
            generation_mode = client.generation_mode if client.is_configured else "demo"
        except Exception as exc:
            logger.exception("Digest generation failed: %s", exc)
            if not allow_demo:
                if quality_report is not None:
                    quality_report.append(
                        {
                            "title_original": candidate.get("title_original", ""),
                            "source": candidate.get("source", ""),
                            "url": candidate.get("url", ""),
                            "issue": f"模型生成失败：{exc}",
                            "generation_mode": "failed",
                            "source_text_chars": len(source_text),
                        }
                    )
                continue
            digest = ensure_chinese_digest(mock_digest(candidate), candidate)
            generation_mode = "demo_fallback"
        if stop_event is not None and stop_event.is_set():
            logger.info("Digest generation stopped after current model call.")
            break
        digest["candidate_id"] = str(candidate.get("candidate_id", ""))
        digest["source"] = str(candidate.get("source", ""))
        digest["url"] = str(candidate.get("url", ""))
        digest["source_text_chars"] = len(source_text)
        digest["generation_mode"] = generation_mode
        quality_issue = digest_quality_issue(digest)
        if quality_issue:
            logger.warning(
                "Digest skipped by quality gate for %s: %s",
                candidate.get("url", ""),
                quality_issue,
            )
            if quality_report is not None:
                quality_report.append(
                    {
                        "title_original": candidate.get("title_original", ""),
                        "source": candidate.get("source", ""),
                        "url": candidate.get("url", ""),
                        "issue": quality_issue,
                        "generation_mode": generation_mode,
                        "source_text_chars": len(source_text),
                    }
                )
            continue
        digests.append(digest)
    return digests


def save_final_json(digests: list[dict[str, Any]], run_date: str) -> Path:
    path = final_json_path(run_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(digests, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_final_json(run_date: str) -> list[dict[str, Any]]:
    path = final_json_path(run_date)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _generate_one(
    candidate: dict[str, Any], prompt: str, client: OpenAIClient
) -> dict[str, Any]:
    if not client.is_configured:
        return ensure_chinese_digest(mock_digest(candidate), candidate)

    payload = {
        "title_original": candidate.get("title_original", ""),
        "source": candidate.get("source", ""),
        "url": candidate.get("url", ""),
        "published_date": candidate.get("published_date", ""),
        "language": candidate.get("language", ""),
        "matched_keywords": candidate.get("matched_keywords", ""),
        "suggested_type": candidate.get("suggested_type", ""),
        "suggested_soft_hard": candidate.get("suggested_soft_hard", ""),
        "news_text": _source_text(candidate),
    }
    result = client.generate_json(prompt, payload)
    digest = normalize_digest(result, candidate)
    if not digest_quality_issue(digest):
        return digest
    if not load_profile().require_simplified_chinese:
        return digest

    logger.warning(
        "Digest failed quality checks, retrying Chinese repair for %s",
        candidate.get("url", ""),
    )
    previous = digest
    for attempt in range(1, CHINESE_REPAIR_ATTEMPTS + 1):
        try:
            cleanup_only = attempt > 1 and needs_chinese_repair(previous)
            repair_prompt = CHINESE_CLEANUP_PROMPT if cleanup_only else CHINESE_REPAIR_PROMPT
            repair_payload = (
                {
                    "title_cn": previous.get("title_cn", ""),
                    "summary_cn": previous.get("summary_cn", ""),
                    "keywords": previous.get("keywords", []),
                    "type": previous.get("type", ""),
                    "soft_hard": previous.get("soft_hard", ""),
                }
                if cleanup_only
                else {
                    **payload,
                    "news_text": _source_text(candidate)[:5000],
                    "previous_title_cn": previous.get("title_cn", ""),
                    "previous_summary_cn": previous.get("summary_cn", ""),
                    "repair_attempt": attempt,
                }
            )
            repair_result = client.generate_json(
                repair_prompt,
                repair_payload,
            )
            repaired = normalize_digest(repair_result, candidate)
            if not digest_quality_issue(repaired):
                return repaired
            previous = repaired
        except Exception as exc:
            logger.exception("Chinese digest repair failed on attempt %s: %s", attempt, exc)

    return fallback_chinese_digest(candidate)


def _source_text(candidate: dict[str, Any]) -> str:
    return clean_text(candidate.get("raw_text", "") or candidate.get("raw_text_preview", ""))


def normalize_digest(result: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    profile = load_profile()
    title = clean_text(result.get("title_cn") or candidate.get("title_original", ""))
    summary = clean_text(result.get("summary_cn") or candidate.get("raw_text_preview", ""))
    keywords = result.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [word.strip() for word in keywords.split(",") if word.strip()]
    digest_type = str(result.get("type") or candidate.get("suggested_type") or "技术")
    soft_hard = str(
        result.get("soft_hard") or candidate.get("suggested_soft_hard") or "硬科学"
    )
    if profile.require_simplified_chinese and digest_type not in {"政策", "技术", "产业"}:
        digest_type = "技术"
    if profile.require_simplified_chinese and soft_hard not in {"软科学", "硬科学"}:
        soft_hard = "硬科学"
    return {
        "title_cn": title,
        "summary_cn": summary[: profile.max_summary_chars],
        "keywords": keywords,
        "type": digest_type,
        "soft_hard": soft_hard,
    }


def ensure_chinese_digest(digest: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    if needs_chinese_repair(digest):
        return fallback_chinese_digest(candidate)
    return digest


def needs_chinese_repair(digest: dict[str, Any]) -> bool:
    if not load_profile().require_simplified_chinese:
        return False
    title = clean_text(digest.get("title_cn", ""))
    summary = clean_text(digest.get("summary_cn", ""))
    if not title or not summary:
        return True
    if not CHINESE_CHAR_RE.search(title) or not CHINESE_CHAR_RE.search(summary):
        return True
    return bool(FOREIGN_SCRIPT_RE.search(f"{title} {summary}"))


def digest_quality_issue(digest: dict[str, Any]) -> str:
    """Reject placeholder or malformed summaries before they reach Word output."""
    profile = load_profile()
    title = clean_text(digest.get("title_cn", ""))
    summary = clean_text(digest.get("summary_cn", ""))
    if not title or not summary:
        return "标题或摘要为空"
    if profile.require_simplified_chinese and needs_chinese_repair(digest):
        return "标题或摘要不是完整简体中文"
    combined = f"{title} {summary}"
    marker = next(
        (item for item in LOW_QUALITY_DIGEST_MARKERS if item in combined),
        "",
    )
    if marker:
        return f"检测到通用占位文字：{marker}"
    if profile.require_simplified_chinese and len(CHINESE_CHAR_RE.findall(summary)) < 45:
        return "摘要中的有效中文信息过少"
    if not profile.require_simplified_chinese and len(summary) < 80:
        return "Summary contains too little information"
    return ""


def fallback_chinese_digest(candidate: dict[str, Any]) -> dict[str, Any]:
    existing_title = clean_text(
        candidate.get("mock_title_cn")
        or candidate.get("title_cn")
        or candidate.get("title_translated_candidate")
    )
    if (
        existing_title
        and CHINESE_CHAR_RE.search(existing_title)
        and not FOREIGN_SCRIPT_RE.search(existing_title)
    ):
        title = existing_title
    else:
        source = clean_text(candidate.get("source", "")) or "相关机构"
        topic = _fallback_topic(candidate)
        title = f"{source}发布{topic}相关科技动态"

    source = clean_text(candidate.get("source", "")) or "相关机构"
    topic = _fallback_topic(candidate)
    keywords = _fallback_keywords(candidate)
    keyword_text = "、".join(keywords[:4]) if keywords else topic
    summary = (
        f"{source}发布一项与{keyword_text}相关的科技动态。该消息来自已选候选新闻，"
        "涉及科技政策、产业应用或前沿技术进展，可作为当日科技要闻线索，建议结合原文链接复核具体细节。"
    )
    return {
        "title_cn": title,
        "summary_cn": summary[:260],
        "keywords": keywords[:8],
        "type": str(candidate.get("suggested_type") or "技术"),
        "soft_hard": str(candidate.get("suggested_soft_hard") or "硬科学"),
    }


def _fallback_topic(candidate: dict[str, Any]) -> str:
    digest_type = str(candidate.get("suggested_type") or "").strip()
    keywords = _fallback_keywords(candidate)
    if keywords:
        return keywords[0]
    if digest_type in {"政策", "技术", "产业"}:
        return digest_type
    return "科技"


def _fallback_keywords(candidate: dict[str, Any]) -> list[str]:
    raw_keywords = candidate.get("keywords") or candidate.get("matched_keywords") or ""
    if isinstance(raw_keywords, list):
        parts = [str(item) for item in raw_keywords]
    else:
        parts = re.split(r"[,;，；、]", str(raw_keywords))
    keywords: list[str] = []
    for part in parts:
        word = clean_text(part.split(":")[-1])
        if word and word not in keywords and not FOREIGN_SCRIPT_RE.search(word):
            keywords.append(word)
    return keywords


def mock_digest(candidate: dict[str, Any]) -> dict[str, Any]:
    mock_title = clean_text(candidate.get("mock_title_cn") or candidate.get("title_cn"))
    mock_summary = clean_text(
        candidate.get("mock_summary_cn") or candidate.get("summary_cn")
    )
    if mock_title and mock_summary:
        keywords = candidate.get("mock_keywords") or candidate.get("keywords") or []
        if isinstance(keywords, str):
            keywords = [
                word.strip()
                for word in keywords.replace(";", ",").split(",")
                if word.strip()
            ]
        return {
            "title_cn": mock_title,
            "summary_cn": mock_summary[:260],
            "keywords": keywords[:8],
            "type": str(candidate.get("suggested_type") or "技术"),
            "soft_hard": str(candidate.get("suggested_soft_hard") or "硬科学"),
        }

    title = clean_text(candidate.get("title_translated_candidate")) or clean_text(
        candidate.get("title_original")
    )
    preview = clean_text(candidate.get("raw_text") or candidate.get("raw_text_preview"))
    if len(preview) > 200:
        preview = preview[:200].rstrip() + "。"
    if not preview:
        preview = "该新闻包含科技政策、产业或技术动态信息，建议人工复核后补充摘要正文。"
    keywords_text = str(candidate.get("matched_keywords", ""))
    keywords = []
    for part in keywords_text.replace(";", ",").split(","):
        word = part.split(":")[-1].strip()
        if word:
            keywords.append(word)
    return {
        "title_cn": title,
        "summary_cn": preview,
        "keywords": keywords[:8],
        "type": str(candidate.get("suggested_type") or "技术"),
        "soft_hard": str(candidate.get("suggested_soft_hard") or "硬科学"),
    }
