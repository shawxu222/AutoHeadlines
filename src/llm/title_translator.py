from __future__ import annotations

import re
from typing import Any

from src.llm.openai_client import OpenAIClient
from src.parsers.text_cleaner import clean_text
from src.utils.logger import get_logger


logger = get_logger(__name__)


def translate_korean_candidate_titles(
    candidates: list[dict[str, Any]],
    batch_size: int = 20,
    stop_event: Any | None = None,
) -> list[dict[str, Any]]:
    """Fill title_translated_candidate for Korean candidate rows when an LLM is available."""
    targets = []
    for candidate in candidates:
        if _stop_requested(stop_event):
            return candidates
        title = clean_text(candidate.get("title_original", ""))
        translated = clean_text(candidate.get("title_translated_candidate", ""))
        language = str(candidate.get("language", "")).lower()
        if not _needs_korean_translation(title, translated, language):
            continue
        targets.append(
            {
                "candidate_id": str(candidate.get("candidate_id", "")),
                "title": title,
            }
        )

    if not targets:
        return candidates

    client = OpenAIClient()
    if not client.is_configured:
        return candidates

    translations: dict[str, str] = {}
    for index in range(0, len(targets), batch_size):
        if _stop_requested(stop_event):
            return candidates
        batch = targets[index : index + batch_size]
        try:
            translations.update(_translate_batch(client, batch))
        except Exception as exc:
            logger.exception("Korean title translation failed: %s", exc)
        if _stop_requested(stop_event):
            return candidates

    unresolved = [
        target for target in targets if target["candidate_id"] not in translations
    ]
    retry_size = max(1, min(5, batch_size))
    for index in range(0, len(unresolved), retry_size):
        if _stop_requested(stop_event):
            return candidates
        batch = unresolved[index : index + retry_size]
        try:
            translations.update(_translate_batch(client, batch))
        except Exception as exc:
            logger.exception("Korean title translation retry failed: %s", exc)

    unresolved = [
        target for target in targets if target["candidate_id"] not in translations
    ]
    for target in unresolved:
        if _stop_requested(stop_event):
            return candidates
        try:
            translated = _translate_single(client, target["title"])
            if translated:
                translations[target["candidate_id"]] = translated
        except Exception as exc:
            logger.exception("Single Korean title translation failed: %s", exc)

    if not translations:
        return candidates

    for candidate in candidates:
        if _stop_requested(stop_event):
            return candidates
        candidate_id = str(candidate.get("candidate_id", ""))
        if candidate_id in translations:
            candidate["title_translated_candidate"] = translations[candidate_id]
    return candidates


def _stop_requested(stop_event: Any | None) -> bool:
    return bool(stop_event is not None and stop_event.is_set())


def _translate_batch(client: OpenAIClient, batch: list[dict[str, str]]) -> dict[str, str]:
    result = client.generate_json(
        "你是科技新闻标题翻译助手。请把韩语新闻标题翻译成简洁、准确的中文标题。"
        "每个 candidate_id 都必须返回一条翻译；输出中绝对不能出现任何韩文字母。"
        "韩国机构、人名或产品没有公认中文名时，使用拉丁字母转写。"
        "不要补充原文没有的信息。只输出 JSON。",
        {"titles": batch, "output_schema": {"translations": [{"candidate_id": "", "title_cn": ""}]}},
    )
    rows = result.get("translations", [])
    translations: dict[str, str] = {}
    if isinstance(rows, dict):
        translations = {
            str(candidate_id): clean_text(title_cn)
            for candidate_id, title_cn in rows.items()
            if clean_text(title_cn)
        }
    elif isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            candidate_id = str(row.get("candidate_id") or row.get("id") or "")
            title_cn = clean_text(row.get("title_cn") or row.get("translation") or "")
            if candidate_id and title_cn:
                translations[candidate_id] = title_cn

    original_titles = {item["candidate_id"]: item["title"] for item in batch}
    output: dict[str, str] = {}
    for candidate_id, translated in translations.items():
        if candidate_id not in original_titles:
            continue
        normalized = _normalize_translated_title(translated)
        if _is_valid_chinese_translation(normalized, original_titles[candidate_id]):
            output[candidate_id] = normalized
    return output


def _translate_single(client: OpenAIClient, title: str) -> str:
    result = client.generate_json(
        "请把韩语新闻标题完整翻译成简洁、准确的中文标题。"
        "输出中绝对不能出现任何韩文字母；专有名词没有公认中文名时使用拉丁字母转写。"
        "不要解释，只输出 JSON。",
        {"title_original": title, "output_schema": {"title_cn": ""}},
    )
    translated = clean_text(result.get("title_cn") or result.get("translation") or "")
    normalized = _normalize_translated_title(translated)
    return normalized if _is_valid_chinese_translation(normalized, title) else ""


def _normalize_translated_title(translated: str) -> str:
    translated = clean_text(translated)
    if _contains_chinese(translated) and _contains_hangul(translated):
        return re.sub(r"[\uac00-\ud7a3]+", _romanize_hangul_match, translated)
    return translated


def _romanize_hangul_match(match: re.Match[str]) -> str:
    return _romanize_hangul(match.group(0)).capitalize()


def _romanize_hangul(text: str) -> str:
    initials = [
        "g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s",
        "ss", "", "j", "jj", "ch", "k", "t", "p", "h",
    ]
    vowels = [
        "a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa",
        "wae", "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i",
    ]
    finals = [
        "", "k", "k", "ks", "n", "nj", "nh", "t", "l", "lk",
        "lm", "lb", "ls", "lt", "lp", "lh", "m", "p", "ps", "t",
        "t", "ng", "t", "t", "k", "t", "p", "h",
    ]
    output = []
    for character in text:
        syllable = ord(character) - 0xAC00
        if syllable < 0 or syllable > 11171:
            output.append(character)
            continue
        initial_index = syllable // 588
        vowel_index = (syllable % 588) // 28
        final_index = syllable % 28
        output.append(
            initials[initial_index] + vowels[vowel_index] + finals[final_index]
        )
    return "".join(output)


def _needs_korean_translation(title: str, translated: str, language: str) -> bool:
    if not title or (language != "ko" and not _contains_hangul(title)):
        return False
    return not _is_valid_chinese_translation(translated, title)


def _is_valid_chinese_translation(translated: str, original: str) -> bool:
    translated = clean_text(translated)
    original = clean_text(original)
    return bool(
        translated
        and translated != original
        and _contains_chinese(translated)
        and not _contains_hangul(translated)
    )


def _contains_hangul(text: str) -> bool:
    return bool(re.search(r"[\uac00-\ud7a3]", text))


def _contains_chinese(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", text))
