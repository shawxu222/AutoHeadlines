from __future__ import annotations

from src.llm.config_assistant import (
    ask_configuration_assistant,
    normalize_source_suggestion,
    source_config_from_suggestion,
)


class FakeClient:
    def generate_json(self, system_prompt, user_payload):  # noqa: ANN001
        assert "XAutoHeadlines" in system_prompt
        assert user_payload["conversation"][-1]["content"] == "推荐欧美科技网站"
        return {
            "answer": "可以先从官方研究机构开始。",
            "recommended_sources": [
                {
                    "name": "Example Research",
                    "base_url": "https://example.com",
                    "country_region": "US",
                    "language": "en",
                    "source_type": "official",
                    "priority": 9,
                    "discovery_urls": ["https://example.com/news"],
                    "reason": "官方研究新闻。",
                },
                {"name": "Invalid", "base_url": "file:///tmp/private"},
            ],
        }


def test_configuration_assistant_returns_sanitized_structured_sources() -> None:
    result = ask_configuration_assistant(
        [{"role": "user", "content": "推荐欧美科技网站"}],
        client=FakeClient(),
    )

    assert result["answer"] == "可以先从官方研究机构开始。"
    assert len(result["recommended_sources"]) == 1
    assert result["recommended_sources"][0]["priority"] == 5
    assert not result["recommended_sources"][0]["enabled"]


def test_source_config_removes_assistant_reason_and_can_enable() -> None:
    source = source_config_from_suggestion(
        {
            "name": "Example",
            "base_url": "https://example.com",
            "reason": "Helpful",
        },
        enabled=True,
    )

    assert source["enabled"]
    assert "reason" not in source
    assert source["discovery_urls"] == ["https://example.com"]


def test_invalid_source_suggestion_is_rejected() -> None:
    assert normalize_source_suggestion({"name": "Local", "base_url": "localhost"}) is None
