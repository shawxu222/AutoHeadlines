from __future__ import annotations

from pathlib import Path

import pytest

from src import config_loader
from src.config_loader import load_profile
from src.llm.digest_generator import generate_digests


def test_builtin_profile_is_release_safe() -> None:
    profile = load_profile()

    assert profile.profile_id == "japan-korea-scitech-zh"
    assert profile.sources_file.exists()
    assert profile.keywords_file.exists()
    assert profile.prompt_file.exists()
    assert "Private Organization" not in profile.digest_title_template


def test_custom_profile_can_change_output_and_window(tmp_path: Path) -> None:
    path = tmp_path / "profile.yaml"
    path.write_text(
        """
id: custom
name: Custom
sources_file: config/sources.yaml
keywords_file: config/keywords.yaml
date_window:
  timezone_label: UTC
  start_hour: 6
summary:
  output_language: en
  require_simplified_chinese: false
  max_summary_chars: 900
  prompt_file: prompts/digest_prompt_en.md
output:
  digest_title_template: "Digest {date}:"
  word_font: Arial
""".strip(),
        encoding="utf-8",
    )

    profile = load_profile(path)

    assert profile.output_language == "en"
    assert profile.window_start_hour == 6
    assert profile.digest_title_template == "Digest {date}:"


def test_summary_generation_requires_a_configured_model(monkeypatch) -> None:
    class UnconfiguredClient:
        is_configured = False
        generation_mode = "mock"

    monkeypatch.delenv("AUTOHEADLINES_ALLOW_DEMO_SUMMARIES", raising=False)

    with pytest.raises(RuntimeError, match="No LLM provider is configured"):
        generate_digests([], "2026-06-12", client=UnconfiguredClient())


def test_model_failure_does_not_silently_create_a_demo_summary(monkeypatch) -> None:
    class BrokenClient:
        is_configured = True
        generation_mode = "broken"

        def generate_json(self, system_prompt, user_payload):  # noqa: ANN001
            raise RuntimeError("provider unavailable")

    monkeypatch.delenv("AUTOHEADLINES_ALLOW_DEMO_SUMMARIES", raising=False)
    report: list[dict[str, object]] = []

    digests = generate_digests(
        [
            {
                "candidate_id": "example",
                "title_original": "Example technology news",
                "source": "Example",
                "url": "https://example.com/news",
                "raw_text": "A detailed article about a concrete technology development.",
            }
        ],
        "2026-06-12",
        client=BrokenClient(),
        quality_report=report,
    )

    assert digests == []
    assert report[0]["generation_mode"] == "failed"


def test_private_settings_can_point_master_and_learning_to_same_word(
    tmp_path: Path, monkeypatch
) -> None:
    word_path = tmp_path / "private-master.docx"
    word_path.touch()
    settings_path = tmp_path / "user_settings.json"
    settings_path.write_text(
        (
            '{"master_docx_path": "%s", "reference_docx_path": "%s", '
            '"digest_title_template": "Private {date}:"}'
            % (word_path, word_path)
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_loader, "PRIVATE_SETTINGS_PATH", settings_path)

    assert config_loader.master_docx_path() == word_path
    assert config_loader.reference_docx_path() == word_path
    assert config_loader.digest_title_template() == "Private {date}:"
