from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlparse

from src.config_loader import KeywordEntry, load_profile
from src.parsers.text_cleaner import compact_for_preview, clean_text
from src.scoring.keyword_scorer import (
    keyword_score,
    match_keywords,
    matched_keywords_text,
)
from src.scoring.similarity_scorer import title_similarity


INFO_PATTERNS = [
    r"\d+(\.\d+)?\s?(billion|million|trillion|亿美元|亿日元|万亿|兆|%|％)",
    r"\d{4}年|\d{4}-\d{1,2}-\d{1,2}|FY\d{4}|20\d{2}",
    r"roadmap|timeline|target|pilot|trial|clinical|量产|投资|预算|计划|工厂",
    r"nm|qubit|MW|GW|GWh|TOPS|FLOPS|nanometer",
]
STRATEGIC_TERMS = [
    "national strategy",
    "economic security",
    "supply chain",
    "export control",
    "international cooperation",
    "research infrastructure",
    "国家战略",
    "经济安全",
    "供应链",
    "出口管制",
    "国际合作",
    "科研基础设施",
    "人才",
]
NOVELTY_TERMS = [
    "first",
    "world's first",
    "breakthrough",
    "launch",
    "unveil",
    "mass production",
    "首次",
    "首个",
    "全球首个",
    "突破",
    "发布",
    "量产",
    "重大",
]
POLICY_CATEGORIES = {"科技政策"}
INDUSTRY_CATEGORIES = {"产业动态", "半导体"}
NEGATIVE_TERMS = [
    "stock price",
    "share price",
    "sports",
    "entertainment",
    "accident",
    "株価",
    "人事",
    "芸能",
    "スポーツ",
    "事故",
    "娱乐",
    "体育",
    "股价",
    "人事变动",
    "고객센터",
    "콜센터",
    "파업",
    "노사",
    "ksqi",
]
TARGET_REGION_TERMS = [
    "japan",
    "japanese",
    "日本",
    "日本政府",
    "国内",
    "経産省",
    "文科省",
    "総務省",
    "内閣府",
    "経済産業省",
    "経産省",
    "meti",
    "nedo",
    "科学技術振興機構",
    "jst",
    "nedo",
    "理化学研究所",
    "産総研",
    "東京大学",
    "京都大学",
    "大阪大学",
    "東北大学",
    "九州大学",
    "東京科学大学",
    "北九州",
    "茨城",
    "つくば",
    "ソフトバンク",
    "softbank",
    "ntt",
    "nec",
    "富士通",
    "fujitsu",
    "ソニー",
    "sony",
    "パナソニック",
    "panasonic",
    "トヨタ",
    "toyota",
    "ホンダ",
    "honda",
    "日立",
    "hitachi",
    "三菱",
    "renesas",
    "ルネサス",
    "rapidus",
    "東京エレクトロン",
    "tokyo electron",
    "sumco",
    "信越化学",
    "住友化学",
    "tokium",
    "korea",
    "korean",
    "韓国",
    "韩国",
    "한국",
    "kaist",
    "서울대",
    "서울대학교",
    "samsung",
    "サムスン",
    "삼성",
    "sk hynix",
    "skハイニックス",
    "lg",
    "hyundai",
    "현대",
]
LOW_PRIORITY_GLOBAL_TERMS = [
    "walmart",
    "ウォルマート",
    "netflix",
    "ネットフリックス",
    "wikipedia",
    "ウィキペディア",
    "中国・",
    "中国新興企業",
]
LOW_VALUE_BUSINESS_TERMS = [
    "決算",
    "営業利益",
    "マーケティングリサーチ",
    "消費者理解",
    "動画配信",
    "小売り",
    "店舗",
    "リテール",
]
LOW_SCOPE_CONSUMER_TERMS = [
    "たこ焼き",
    "章鱼烧",
    "冷凍食品",
    "食品メーカー",
    "食品会社",
    "外食",
    "飲食",
    "商店街",
    "商業施設",
    "小売",
    "スーパー",
    "コンビニ",
    "レストラン",
    "居酒屋",
    "菓子",
    "スイーツ",
    "冷凍たこ焼き",
    "스타벅스",
]
LOW_VALUE_OPERATIONAL_TERMS = [
    "고객센터",
    "콜센터",
    "우수콜센터",
    "고객감동",
    "ksqi",
    "파업",
    "노사 협상",
    "공동파업",
]
MARKET_TITLE_TERMS = [
    "日経平均",
    "東証",
    "株価",
    "株式",
    "下値",
    "上値",
    "バリュー物色",
    "循環物色",
    "買い",
    "売り",
    "円相場",
    "ドル円",
    "日本円",
    "為替",
    "時価総額",
    "純利益",
    "決算",
    "営業益",
    "증시",
    "주가",
    "환율",
    "특징주",
    "목표가",
    "레버리지",
    "급등",
    "황제주",
    "단일종목",
]
MARKET_BODY_TERMS = [
    "投資家",
    "市場関係者",
    "前場",
    "後場",
    "大引け",
    "午前終値",
    "終値",
    "上昇率",
    "下落率",
    "指数",
    "マーケット",
    "market close",
    "stock market",
    "share price",
]
SPORTS_TITLE_TERMS = [
    "ゴルフ",
    "卓球",
    "サッカー",
    "野球",
    "バスケットボール",
    "バスケ",
    "テニス",
    "ラグビー",
    "オリンピック",
    "パラリンピック",
    "ワールドカップ",
    "jリーグ",
    "大リーグ",
    "プロ野球",
    "골프",
    "탁구",
    "축구",
    "야구",
    "농구",
    "테니스",
    "럭비",
    "올림픽",
    "월드컵",
    "高尔夫",
    "乒乓球",
    "足球",
    "棒球",
    "篮球",
    "网球",
    "橄榄球",
    "奥运会",
    "世界杯",
    "golf",
    "table tennis",
    "soccer",
    "football",
    "baseball",
    "basketball",
    "tennis",
    "rugby",
    "olympic",
    "world cup",
]
SPORTS_TECH_TITLE_TERMS = [
    "ai",
    "人工知能",
    "人工智能",
    "센서",
    "sensor",
    "センサー",
    "分析",
    "解析",
    "analytics",
    "technology",
    "技術",
    "技术",
    "테크",
    "ロボット",
    "robot",
    "材料",
    "素材",
    "デバイス",
    "device",
    "気候",
    "高温",
    "予測",
    "研究",
    "climate",
    "weather",
    "forecast",
    "prediction",
    "气候",
    "天气",
    "高温",
    "预测",
    "研究",
    "기후",
    "날씨",
    "고온",
    "예측",
    "연구",
]
CORE_SCITECH_TERMS = [
    "ai",
    "人工知能",
    "生成ai",
    "半導体",
    "半导体",
    "gpu",
    "量子",
    "水素",
    "蓄電",
    "電池",
    "核融合",
    "バイオ",
    "医療",
    "創薬",
    "遺伝子",
    "ロボット",
    "自動化",
    "宇宙",
    "衛星",
    "新材料",
    "新素材",
    "触媒",
    "ペロブスカイト",
    "研究開発",
    "r&d",
    "経済安全",
    "サプライチェーン",
    "データセンター",
    "科研",
    "科学技術",
    "大学",
    "研究チーム",
]
HIGH_VALUE_POLICY_TERMS = [
    "文部科学省",
    "文科省",
    "mext",
    "公募",
    "研究支援",
    "ai for science",
    "計算資源",
    "戦略的増強",
    "研究開発",
    "科研",
    "科学技術",
    "研究基盤",
    "hpci",
    "次世代hpc",
    "研究インフラ",
    "研究設備",
    "競争的資金",
    "経済産業省",
    "経産省",
    "meti",
    "nedo",
    "jst",
    "科学技術振興機構",
    "内閣府",
    "csti",
    "科学技術・イノベーション",
    "採択",
    "委託事業",
    "補助金",
    "基金",
    "実証",
    "戦略プログラム",
    "ムーンショット",
]


def candidate_id_for(article: dict[str, Any]) -> str:
    key = clean_text(article.get("url") or article.get("title_original") or "")
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def score_candidate(
    article: dict[str, Any],
    keywords: list[KeywordEntry],
    accepted_samples: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    title = clean_text(article.get("title_original"))
    text = clean_text(article.get("raw_text"))
    warnings = clean_text(article.get("extraction_warning"))
    combined = f"{title} {text} {warnings}"

    matches = match_keywords(combined, keywords)
    relevance = keyword_score(matches, 30)
    info = _score_info_value(combined)
    strategy = _score_strategic(combined)
    novelty = _score_novelty(combined)
    authority = _score_authority(article)
    compilable = _score_compilable(title, text)
    high_value_policy = _score_high_value_policy(article, combined)
    region_bonus, region_penalty, region_reasons = _score_target_region(
        article, combined
    )

    similarity_bonus = min(5.0, title_similarity(title, accepted_samples or []) * 5)
    reference_bonus = _score_reference_preference(matches, accepted_samples or [])
    reference_score_raw = similarity_bonus + reference_bonus
    reference_similarity_score = (
        round(min(10.0, reference_score_raw) / 10 * 100, 1)
        if reference_score_raw
        else 0
    )
    negative_penalty = _score_negative_penalty(combined)
    low_scope_consumer = _is_low_scope_consumer_news(combined)
    excluded_topic = is_excluded_topic(article)
    total = min(
        100.0,
        max(
            0.0,
            relevance
            + info
            + strategy
            + novelty
            + authority
            + compilable
            + high_value_policy
            + region_bonus
            + similarity_bonus
            + reference_bonus
            - negative_penalty
            - region_penalty,
        ),
    )
    if low_scope_consumer:
        total = min(total, 32.0)
    if excluded_topic:
        total = 0.0

    reasons = _reasons(
        relevance, info, strategy, novelty, authority, compilable, similarity_bonus
    )
    if high_value_policy:
        reasons.append("重点政策/科研资助信息")
    if reference_bonus:
        reasons.append("符合历史采纳偏好")
    reasons.extend(region_reasons)
    if negative_penalty:
        reasons.append("低相关内容降权")
    if low_scope_consumer:
        reasons.append("普通消费/食品商业新闻降权")
    if excluded_topic:
        reasons.append("非科委要闻主题，已排除")
    if region_penalty:
        reasons.append("非当前 Profile 重点地区内容降权")
    suggested_type = suggest_type(matches, combined)
    suggested_soft_hard = suggest_soft_hard(matches, combined)

    return {
        "candidate_id": candidate_id_for(article),
        "score": round(total, 1),
        "recommended_reason": "；".join(reasons),
        "title_original": title,
        "title_translated_candidate": "",
        "source": article.get("source", ""),
        "country_region": article.get("country_region", ""),
        "language": article.get("language", ""),
        "published_date": article.get("published_date", ""),
        "url": article.get("url", ""),
        "source_domain": article.get("source_domain") or urlparse(article.get("url", "")).netloc,
        "source_section": article.get("source_section", ""),
        "source_type": article.get("source_type", ""),
        "source_priority": article.get("source_priority", ""),
        "max_articles_per_run": article.get("max_articles_per_run", ""),
        "matched_keywords": matched_keywords_text(matches),
        "reference_similarity_score": reference_similarity_score,
        "suggested_type": suggested_type,
        "suggested_soft_hard": suggested_soft_hard,
        "raw_text_preview": compact_for_preview(text),
        "raw_text": text,
        "extraction_warning": article.get("extraction_warning", ""),
        "selected": "",
        "notes": "",
    }


def deduplicate_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_url: dict[str, dict[str, Any]] = {}
    groups: list[list[dict[str, Any]]] = []

    for candidate in sorted(candidates, key=lambda item: item.get("score", 0), reverse=True):
        url = candidate.get("url", "")
        if url and url in by_url:
            existing = by_url[url]
            group = existing.setdefault("_duplicates", [existing])
            group.append(candidate)
            continue
        matched_group = None
        for group in groups:
            if _title_group_match(candidate, group[0]):
                matched_group = group
                break
        if matched_group is not None:
            matched_group.append(candidate)
        else:
            groups.append([candidate])
        if url:
            by_url[url] = candidate

    output = []
    for index, group in enumerate(groups, start=1):
        group = sorted(group, key=_dedupe_rank, reverse=True)
        kept = group[0]
        kept["duplicate_group"] = f"D{index:03d}" if len(group) > 1 else ""
        output.append(kept)
    return sorted(output, key=lambda item: item.get("score", 0), reverse=True)


def _title_group_match(a: dict[str, Any], b: dict[str, Any]) -> bool:
    title_a = clean_text(a.get("title_original"))
    title_b = clean_text(b.get("title_original"))
    if not title_a or not title_b:
        return False
    return SequenceMatcher(None, title_a, title_b).ratio() >= 0.86


def _dedupe_rank(candidate: dict[str, Any]) -> tuple[float, int, int]:
    official_bonus = 10 if candidate.get("source_type") == "official" else 0
    return (
        float(candidate.get("score", 0)) + official_bonus,
        len(candidate.get("raw_text", "")),
        len(candidate.get("matched_keywords", "")),
    )


def _score_info_value(text: str) -> float:
    count = 0
    for pattern in INFO_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            count += 1
    length_bonus = 1 if len(text) > 800 else 0
    return min(25.0, count * 5 + length_bonus * 5)


def _score_strategic(text: str) -> float:
    lower = text.lower()
    count = sum(1 for term in STRATEGIC_TERMS if term.lower() in lower)
    return min(20.0, count * 5)


def _score_novelty(text: str) -> float:
    lower = text.lower()
    count = sum(1 for term in NOVELTY_TERMS if term.lower() in lower)
    return min(10.0, count * 3)


def _score_authority(article: dict[str, Any]) -> float:
    source_type = str(article.get("source_type", "")).lower()
    priority = int(article.get("source_priority") or 1)
    if source_type == "official":
        return min(10.0, 5 + priority)
    if source_type == "rss":
        return min(10.0, 3 + priority)
    if source_type == "login_browser":
        return min(6.0, 2 + priority * 0.6)
    return min(10.0, priority * 1.5)


def _score_compilable(title: str, text: str) -> float:
    if title and len(text) >= 500:
        return 5.0
    if title and len(text) >= 200:
        return 3.0
    if title and text:
        return 1.0
    return 0.0


def _score_high_value_policy(article: dict[str, Any], text: str) -> float:
    lower = text.lower()
    hits = sum(1 for term in HIGH_VALUE_POLICY_TERMS if term.lower() in lower)
    if not hits:
        return 0.0
    bonus = min(12.0, hits * 3.0)
    if str(article.get("source_type", "")).lower() == "official":
        bonus += 4.0
    if any(term in lower for term in ["ai", "人工知能", "生成ai", "計算資源"]):
        bonus += 3.0
    return min(18.0, bonus)


def _score_negative_penalty(text: str) -> float:
    lower = text.lower()
    penalty = sum(4 for term in NEGATIVE_TERMS if term.lower() in lower)
    penalty += sum(3 for term in LOW_VALUE_BUSINESS_TERMS if term.lower() in lower)
    penalty += sum(8 for term in LOW_SCOPE_CONSUMER_TERMS if term.lower() in lower)
    penalty += sum(12 for term in MARKET_TITLE_TERMS if term.lower() in lower)
    penalty += sum(5 for term in MARKET_BODY_TERMS if term.lower() in lower)
    if any(term in lower for term in ["missing_or_unparsed_date", "missing_published_datetime"]):
        penalty += 8
    if len(text) < 150:
        penalty += 6
    return min(35.0, penalty)


def is_excluded_topic(article: dict[str, Any]) -> bool:
    title = clean_text(article.get("title_original"))
    text = clean_text(article.get("raw_text") or article.get("raw_text_preview"))
    combined = f"{title} {text}"
    return (
        _is_market_only_news(title, text)
        or _is_sports_news(title)
        or _is_low_value_operational_title(title)
        or _is_low_scope_consumer_news(combined)
        or _is_low_value_operational_news(combined)
    )


def _is_sports_news(title: str) -> bool:
    lower = title.lower()
    if not any(term.lower() in lower for term in SPORTS_TITLE_TERMS):
        return False
    return not any(term.lower() in lower for term in SPORTS_TECH_TITLE_TERMS)


def _is_market_only_news(title: str, text: str) -> bool:
    title_lower = title.lower()
    text_lower = text.lower()
    title_hits = [term for term in MARKET_TITLE_TERMS if term.lower() in title_lower]
    if len(title_hits) >= 1:
        return True
    body_hits = sum(1 for term in MARKET_BODY_TERMS if term.lower() in text_lower)
    market_hits = sum(1 for term in MARKET_TITLE_TERMS if term.lower() in text_lower)
    if body_hits + market_hits < 3:
        return False
    title_has_core_scitech = any(term.lower() in title_lower for term in CORE_SCITECH_TERMS)
    body_core_hits = sum(1 for term in CORE_SCITECH_TERMS if term.lower() in text_lower)
    return not title_has_core_scitech and body_core_hits < 2


def _is_low_scope_consumer_news(text: str) -> bool:
    lower = text.lower()
    has_consumer_signal = any(term.lower() in lower for term in LOW_SCOPE_CONSUMER_TERMS)
    if not has_consumer_signal:
        return False
    has_core_scitech_signal = any(term.lower() in lower for term in CORE_SCITECH_TERMS)
    return not has_core_scitech_signal


def _is_low_value_operational_news(text: str) -> bool:
    lower = text.lower()
    if not any(term.lower() in lower for term in LOW_VALUE_OPERATIONAL_TERMS):
        return False
    strategic_terms = [
        "인수",
        "투자",
        "개발",
        "연구",
        "반도체",
        "바이오",
        "양자",
        "데이터센터",
        "공급망",
        "정책",
        "r&d",
    ]
    return not any(term.lower() in lower for term in strategic_terms)


def _is_low_value_operational_title(title: str) -> bool:
    lower = title.lower()
    return any(term.lower() in lower for term in LOW_VALUE_OPERATIONAL_TERMS)


def _score_reference_preference(
    matches: list[dict[str, object]], samples: list[dict[str, str]]
) -> float:
    if not matches or not samples:
        return 0.0
    matched_terms = {
        str(item.get("keyword", "")).strip().lower()
        for item in matches
        if str(item.get("keyword", "")).strip()
    }
    if not matched_terms:
        return 0.0
    historical_terms: set[str] = set()
    for sample in samples:
        text = clean_text(sample.get("keywords", "")).lower()
        historical_terms.update(
            term.strip()
            for term in re.split(r"[,;；、\s]+", text)
            if len(term.strip()) >= 2
        )
    overlap = matched_terms & historical_terms
    return min(5.0, len(overlap) * 1.5)


def _score_target_region(
    article: dict[str, Any], text: str
) -> tuple[float, float, list[str]]:
    """Apply the active profile's optional regional preference."""
    profile = load_profile()
    if not profile.preferred_regions and not profile.region_terms:
        return 0.0, 0.0, []

    lower = text.lower()
    source_region = str(article.get("country_region", "")).lower()
    profile_terms = profile.region_terms or TARGET_REGION_TERMS
    weak_terms = {region.lower() for region in profile.preferred_regions}
    has_strong_target_entity = any(
        term.lower() in lower
        for term in profile_terms
        if term.lower() not in weak_terms
    )
    has_generic_target = any(term.lower() in lower for term in profile_terms)
    is_low_priority_global = any(
        term.lower() in lower for term in LOW_PRIORITY_GLOBAL_TERMS
    )
    has_target_entity = has_strong_target_entity or (
        has_generic_target and not is_low_priority_global
    )
    source_is_target = source_region in {
        region.lower() for region in profile.preferred_regions
    }
    label = "/".join(profile.preferred_regions) or "profile region"

    bonus = 0.0
    penalty = 0.0
    reasons: list[str] = []
    if has_target_entity:
        bonus += 12.0
        reasons.append(f"{label}相关")
    elif source_is_target:
        bonus += 4.0
        reasons.append(f"{label}来源")
    elif profile.require_preferred_region:
        penalty += 18.0

    if is_low_priority_global:
        penalty += 10.0 if not has_target_entity else 4.0

    return min(15.0, bonus), min(25.0, penalty), reasons


def _reasons(*scores: float) -> list[str]:
    labels = [
        "方向相关",
        "信息具体",
        "具战略意义",
        "有新颖性",
        "来源较权威",
        "材料可编译",
        "接近历史采纳样本",
    ]
    return [label for label, score in zip(labels, scores) if score > 0]


def suggest_type(matches: list[dict[str, object]], text: str) -> str:
    categories = {str(item["category"]) for item in matches}
    lower = text.lower()
    if categories & POLICY_CATEGORIES or "policy" in lower or "政策" in text:
        return "政策"
    if categories & INDUSTRY_CATEGORIES or any(
        term in lower for term in ["investment", "factory", "production", "acquisition"]
    ):
        return "产业"
    return "技术"


def suggest_soft_hard(matches: list[dict[str, object]], text: str) -> str:
    categories = {str(item["category"]) for item in matches}
    if "科技政策" in categories or any(term in text for term in ["政策", "战略", "预算"]):
        return "软科学"
    return "硬科学"


def _safe_json_tags(tags_json: Any, tags: Any) -> list[str]:
    if isinstance(tags, list):
        return [str(tag) for tag in tags]
    if isinstance(tags_json, str):
        try:
            import json

            loaded = json.loads(tags_json)
            if isinstance(loaded, list):
                return [str(tag) for tag in loaded]
        except Exception:
            return []
    return []
