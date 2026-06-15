from __future__ import annotations

from src.config_loader import KeywordEntry
from src.scoring.candidate_scorer import is_excluded_topic, score_candidate


def test_score_candidate_matches_keywords() -> None:
    article = {
        "title_original": "Japan announces AI semiconductor research infrastructure plan",
        "raw_text": "The government announced an AI and semiconductor R&D plan with a 2026 timeline and supply chain goals.",
        "source": "Mock Official",
        "source_type": "official",
        "source_priority": 5,
        "url": "https://example.com/news/1",
    }
    keywords = [
        KeywordEntry(category="AI与数字技术", term="AI", weight=8),
        KeywordEntry(category="半导体", term="semiconductor", weight=9),
        KeywordEntry(category="科技政策", term="R&D", weight=8),
    ]
    result = score_candidate(article, keywords)
    assert result["score"] > 40
    assert "AI" in result["matched_keywords"]
    assert result["suggested_type"] == "政策"


def test_score_candidate_penalizes_local_food_factory_news() -> None:
    article = {
        "title_original": "広島の冷凍たこ焼きメーカーが新工場で東南アジア展開",
        "raw_text": "食品メーカーが冷凍たこ焼きの新工場を建設し、店舗向け販売を増やす。投資額と材料調達を見直す。",
        "source": "Nikkei",
        "source_type": "login_browser",
        "source_priority": 5,
        "country_region": "Japan",
        "url": "https://www.nikkei.com/article/mock",
    }
    keywords = [
        KeywordEntry(category="材料与制造", term="材料", weight=7),
        KeywordEntry(category="产业动态", term="新工場", weight=7),
        KeywordEntry(category="产业动态", term="投資", weight=7),
    ]
    result = score_candidate(article, keywords)
    assert result["score"] <= 32
    assert "普通消费/食品商业新闻降权" in result["recommended_reason"]


def test_score_candidate_excludes_market_news_even_with_ai_terms() -> None:
    article = {
        "title_original": "日経平均午前260円安 6万5000円巡る攻防、下値支えるバリュー物色",
        "raw_text": "株式市場ではAIや半導体関連株に投資家の買いが入った。東証前場の日経平均は下落し、為替と指数が注目された。",
        "source": "Nikkei",
        "source_type": "login_browser",
        "source_priority": 5,
        "country_region": "Japan",
        "url": "https://www.nikkei.com/article/mock-market",
    }
    keywords = [
        KeywordEntry(category="AI与数字技术", term="AI", weight=8),
        KeywordEntry(category="半导体", term="半導体", weight=9),
        KeywordEntry(category="产业动态", term="投資", weight=7),
    ]
    result = score_candidate(article, keywords)
    assert result["score"] == 0
    assert "非科委要闻主题，已排除" in result["recommended_reason"]


def test_sports_result_is_excluded_even_when_body_contains_ai_terms() -> None:
    article = {
        "title_original": "中島啓太20位、金谷拓実は29位 米男子ゴルフ最終日",
        "raw_text": "大会サイトではAIによるデータ分析も紹介した。",
    }

    assert is_excluded_topic(article)


def test_sports_technology_news_is_kept() -> None:
    article = {
        "title_original": "AIセンサーでサッカー選手の動きを解析する新技術",
        "raw_text": "大学研究チームがセンサーと人工知能を開発した。",
    }

    assert not is_excluded_topic(article)


def test_climate_research_about_sports_event_is_kept() -> None:
    article = {
        "title_original": "预测显示世界杯97场比赛或受高温天气负面影响",
        "raw_text": "研究团队使用气候模型分析未来高温风险。",
    }

    assert not is_excluded_topic(article)
