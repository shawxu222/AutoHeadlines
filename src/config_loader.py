from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from src.utils.logger import get_logger


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def _env_value(name: str, legacy_name: str | None = None, default: str = "") -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    if legacy_name:
        legacy = os.getenv(legacy_name, "").strip()
        if legacy:
            return legacy
    return default


DATA_ROOT = Path(
    _env_value("XAUTOHEADLINES_HOME", "AUTOHEADLINES_HOME", str(PROJECT_ROOT))
).expanduser() / "data"
PRIVATE_SETTINGS_PATH = DATA_ROOT / "settings" / "user_settings.json"
logger = get_logger(__name__)
DEFAULT_PROFILE = "japan-korea-scitech-zh"


@dataclass(slots=True)
class NewsSource:
    name: str
    country_region: str
    language: str
    section_url: str
    source_type: str
    requires_login: bool
    priority: int
    base_url: str = ""
    tags: list[str] = field(default_factory=list)
    discovery_urls: list[str] = field(default_factory=list)
    link_selectors: list[str] = field(default_factory=list)
    include_url_patterns: list[str] = field(default_factory=list)
    exclude_url_patterns: list[str] = field(default_factory=list)
    rate_limit_seconds: float = 1.0
    max_articles_per_run: int = 20
    enabled: bool = True


@dataclass(slots=True)
class KeywordEntry:
    category: str
    term: str
    weight: float


@dataclass(slots=True)
class Profile:
    profile_id: str
    name: str
    description: str
    sources_file: Path
    keywords_file: Path
    prompt_file: Path
    timezone_label: str = "local time"
    window_start_hour: int = 10
    monday_lookback_days: int = 3
    normal_lookback_days: int = 1
    preferred_regions: list[str] = field(default_factory=list)
    region_terms: list[str] = field(default_factory=list)
    require_preferred_region: bool = False
    output_language: str = "zh-CN"
    require_simplified_chinese: bool = True
    max_summary_chars: int = 260
    digest_title_template: str = "每日科技要闻报送摘要{date}："
    word_font: str = "Microsoft YaHei"


def _resolve_project_path(value: Any, default: str) -> Path:
    path = Path(str(value or default)).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def active_profile_path() -> Path:
    configured = _env_value(
        "XAUTOHEADLINES_PROFILE", "AUTOHEADLINES_PROFILE", DEFAULT_PROFILE
    )
    path = Path(configured).expanduser()
    if path.suffix.lower() in {".yaml", ".yml"} or path.is_absolute():
        return path if path.is_absolute() else PROJECT_ROOT / path
    return PROJECT_ROOT / "config" / "profiles" / f"{configured}.yaml"


def load_profile(path: Path | None = None) -> Profile:
    profile_path = path or active_profile_path()
    if not profile_path.exists():
        raise FileNotFoundError(
            f"XAutoHeadlines profile not found: {profile_path}. "
            "Set XAUTOHEADLINES_PROFILE to a profile name or YAML path."
        )
    payload = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    date_window = payload.get("date_window") or {}
    scoring = payload.get("scoring") or {}
    summary = payload.get("summary") or {}
    output = payload.get("output") or {}
    return Profile(
        profile_id=str(payload.get("id") or profile_path.stem),
        name=str(payload.get("name") or profile_path.stem),
        description=str(payload.get("description") or ""),
        sources_file=_resolve_project_path(
            payload.get("sources_file"), "config/sources.yaml"
        ),
        keywords_file=_resolve_project_path(
            payload.get("keywords_file"), "config/keywords.yaml"
        ),
        prompt_file=_resolve_project_path(
            summary.get("prompt_file"), "prompts/digest_prompt.md"
        ),
        timezone_label=str(date_window.get("timezone_label") or "local time"),
        window_start_hour=int(date_window.get("start_hour", 10)),
        monday_lookback_days=int(date_window.get("monday_lookback_days", 3)),
        normal_lookback_days=int(date_window.get("normal_lookback_days", 1)),
        preferred_regions=[str(item) for item in scoring.get("preferred_regions", [])],
        region_terms=[str(item) for item in scoring.get("region_terms", [])],
        require_preferred_region=bool(scoring.get("require_preferred_region", False)),
        output_language=str(summary.get("output_language") or "zh-CN"),
        require_simplified_chinese=bool(summary.get("require_simplified_chinese", True)),
        max_summary_chars=int(summary.get("max_summary_chars", 260)),
        digest_title_template=str(
            output.get("digest_title_template") or "每日科技要闻报送摘要{date}："
        ),
        word_font=str(output.get("word_font") or "Microsoft YaHei"),
    )


def active_sources_path() -> Path:
    override = _env_value("XAUTOHEADLINES_SOURCES_FILE", "AUTOHEADLINES_SOURCES_FILE")
    if override:
        return _resolve_project_path(override, str(load_profile().sources_file))
    return load_profile().sources_file


def active_keywords_path() -> Path:
    override = _env_value("XAUTOHEADLINES_KEYWORDS_FILE", "AUTOHEADLINES_KEYWORDS_FILE")
    if override:
        return _resolve_project_path(override, str(load_profile().keywords_file))
    return load_profile().keywords_file


def load_private_settings(path: Path | None = None) -> dict[str, Any]:
    settings_path = path or PRIVATE_SETTINGS_PATH
    if not settings_path.exists():
        return {}
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not read private settings %s: %s", settings_path, exc)
        return {}
    return payload if isinstance(payload, dict) else {}


def master_docx_path() -> Path:
    configured = str(load_private_settings().get("master_docx_path", "")).strip()
    return (
        Path(configured).expanduser()
        if configured
        else DATA_ROOT / "output" / "master_digest.docx"
    )


def reference_docx_path() -> Path:
    configured = str(load_private_settings().get("reference_docx_path", "")).strip()
    return (
        Path(configured).expanduser()
        if configured
        else DATA_ROOT / "reference" / "historical_digest.docx"
    )


def digest_title_template() -> str:
    configured = str(load_private_settings().get("digest_title_template", "")).strip()
    return configured or load_profile().digest_title_template


def load_sources(path: Path | None = None) -> list[NewsSource]:
    path = path or active_sources_path()
    with path.open("r", encoding="utf-8") as file:
        payload = yaml.safe_load(file) or {}

    sources = []
    for item in payload.get("sources", []):
        try:
            sources.append(
                NewsSource(
                    name=str(item["name"]),
                    base_url=str(item.get("base_url") or item.get("section_url") or ""),
                    country_region=str(item.get("country_region", "")),
                    language=str(item.get("language", "")),
                    section_url=str(
                        item.get("section_url")
                        or (item.get("discovery_urls") or [""])[0]
                    ),
                    source_type=str(item.get("source_type", "html")).lower(),
                    requires_login=bool(item.get("requires_login", False)),
                    priority=int(item.get("priority", 1)),
                    tags=[str(tag) for tag in item.get("tags") or []],
                    discovery_urls=[
                        str(url) for url in item.get("discovery_urls") or []
                    ],
                    link_selectors=[
                        str(selector) for selector in item.get("link_selectors") or []
                    ],
                    include_url_patterns=[
                        str(pattern)
                        for pattern in item.get("include_url_patterns") or []
                    ],
                    exclude_url_patterns=[
                        str(pattern)
                        for pattern in item.get("exclude_url_patterns") or []
                    ],
                    rate_limit_seconds=float(item.get("rate_limit_seconds", 1.0)),
                    max_articles_per_run=int(item.get("max_articles_per_run", 20)),
                    enabled=bool(item.get("enabled", True)),
                )
            )
        except Exception as exc:
            logger.exception("Invalid source config skipped: %s", exc)
    return sources


def _normalize_keyword_item(
    category: str, item: str | dict[str, Any], default_weight: float
) -> KeywordEntry:
    if isinstance(item, dict):
        term = str(item.get("term") or item.get("keyword") or "").strip()
        weight = float(item.get("weight", default_weight))
    else:
        term = str(item).strip()
        weight = float(default_weight)
    return KeywordEntry(category=category, term=term, weight=weight)


def load_keywords(path: Path | None = None) -> list[KeywordEntry]:
    path = path or active_keywords_path()
    with path.open("r", encoding="utf-8") as file:
        payload = yaml.safe_load(file) or {}

    entries: list[KeywordEntry] = []
    for category, config in (payload.get("categories") or {}).items():
        default_weight = float(config.get("default_weight", 5))
        for item in config.get("keywords", []):
            entry = _normalize_keyword_item(str(category), item, default_weight)
            if entry.term:
                entries.append(entry)
    return entries
