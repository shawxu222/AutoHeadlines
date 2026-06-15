from __future__ import annotations

from src.llm.title_translator import (
    _is_valid_chinese_translation,
    _needs_korean_translation,
    _normalize_translated_title,
    _translate_batch,
)


class _FakeClient:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result

    def generate_json(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        return self.result


def test_korean_title_with_hangul_translation_is_retried() -> None:
    assert _needs_korean_translation(
        "AI 신약개발 시대",
        "AI 신약개발 시대",
        "ko",
    )


def test_valid_chinese_translation_is_not_retried() -> None:
    assert not _needs_korean_translation(
        "AI 신약개발 시대",
        "新药研发进入AI时代",
        "ko",
    )


def test_translate_batch_rejects_hangul_and_accepts_chinese() -> None:
    client = _FakeClient(
        {
            "translations": [
                {"candidate_id": "bad", "title_cn": "AI 신약개발 시대"},
                {"candidate_id": "good", "title_cn": "新药研发进入AI时代"},
            ]
        }
    )
    batch = [
        {"candidate_id": "bad", "title": "AI 신약개발 시대"},
        {"candidate_id": "good", "title": "반도체 공급망 강화"},
    ]

    translations = _translate_batch(client, batch)

    assert translations == {"good": "新药研发进入AI时代"}
    assert _is_valid_chinese_translation("新药研发进入AI时代", "AI 신약개발 시대")


def test_mixed_translation_romanizes_remaining_korean_proper_noun() -> None:
    translated = _normalize_translated_title(
        "LS전선中标东海岸至首都圈HVDC二期项目"
    )

    assert translated == "LSJeonseon中标东海岸至首都圈HVDC二期项目"
    assert _is_valid_chinese_translation(translated, "LS전선 수주")
