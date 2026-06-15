from __future__ import annotations

from src.llm.digest_generator import (
    digest_quality_issue,
    generate_digests,
    needs_chinese_repair,
)


class FakeClient:
    is_configured = True
    generation_mode = "fake"

    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.calls = 0

    def generate_json(self, system_prompt, user_payload):  # noqa: ANN001
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


def test_generate_digests_retries_when_model_returns_japanese() -> None:
    client = FakeClient(
        [
            {
                "title_cn": "2040年以降の新産業の創出に向け、事業を拡充しました",
                "summary_cn": "NEDOはフロンティア育成事業を6領域に拡充しました。",
                "keywords": [],
                "type": "政策",
                "soft_hard": "软科学",
            },
            {
                "title_cn": "NEDO将前沿培育事业扩大至六个领域",
                "summary_cn": "NEDO为推动2040年以后的新产业创造，将前沿培育事业扩大至六个领域，并新增海洋机器人、脑科技和量子传感等方向。",
                "keywords": ["NEDO", "量子传感"],
                "type": "政策",
                "soft_hard": "软科学",
            },
        ]
    )

    digests = generate_digests([_candidate()], "2026-06-04", client=client)

    assert client.calls == 2
    assert digests[0]["title_cn"] == "NEDO将前沿培育事业扩大至六个领域"
    assert not needs_chinese_repair(digests[0])


def test_generate_digests_retries_generic_placeholder() -> None:
    client = FakeClient(
        [
            {
                "title_cn": "NEDO发布AI相关科技动态",
                "summary_cn": (
                    "NEDO发布一项与AI相关的科技动态。该消息来自已选候选新闻，"
                    "可作为当日科技要闻线索，建议结合原文链接复核具体细节。"
                ),
                "keywords": ["AI"],
                "type": "技术",
                "soft_hard": "硬科学",
            },
            {
                "title_cn": "NEDO扩展前沿培育计划覆盖六个领域",
                "summary_cn": (
                    "日本新能源产业技术综合开发机构为推动2040年后的新产业发展，"
                    "将前沿培育计划扩展至六个领域，新增海洋机器人、脑科技和量子传感等方向，"
                    "以支持高风险前沿研究形成后续研发项目。"
                ),
                "keywords": ["NEDO", "量子传感"],
                "type": "政策",
                "soft_hard": "软科学",
            },
        ]
    )

    digests = generate_digests([_candidate()], "2026-06-04", client=client)

    assert client.calls == 2
    assert digests[0]["title_cn"] == "NEDO扩展前沿培育计划覆盖六个领域"


def test_generate_digests_skips_placeholder_fallback_when_repair_still_invalid() -> None:
    client = FakeClient(
        [
            {
                "title_cn": "2040年以降の新産業の創出に向け、事業を拡充しました",
                "summary_cn": "NEDOはフロンティア育成事業を6領域に拡充しました。",
                "keywords": [],
                "type": "政策",
                "soft_hard": "软科学",
            }
        ]
    )
    quality_report: list[dict[str, object]] = []

    digests = generate_digests(
        [_candidate()],
        "2026-06-04",
        client=client,
        quality_report=quality_report,
    )

    assert client.calls == 3
    assert digests == []
    assert len(quality_report) == 1
    assert "通用占位文字" in str(quality_report[0]["issue"])


def test_digest_quality_gate_rejects_generic_review_placeholder() -> None:
    issue = digest_quality_issue(
        {
            "title_cn": "NEDO发布AI相关科技动态",
            "summary_cn": (
                "NEDO发布一项与AI相关的科技动态。该消息来自已选候选新闻，"
                "可作为当日科技要闻线索，建议结合原文链接复核具体细节。"
            ),
        }
    )

    assert "通用占位文字" in issue


def test_digest_quality_gate_accepts_concrete_summary() -> None:
    issue = digest_quality_issue(
        {
            "title_cn": "NEDO确定数理科学产学合作调查实施单位",
            "summary_cn": (
                "日本新能源产业技术综合开发机构为推动数理科学解决产业问题，"
                "确定由Leave a Nest实施产学合作模式调查。项目将分析几何学和拓扑学等方法的优势领域，"
                "并通过企业与大学匹配梳理潜在技术项目，计划持续至2027年3月。"
            ),
        }
    )

    assert issue == ""


def _candidate() -> dict[str, object]:
    return {
        "title_original": "2040年以降の新産業の創出に向け、「フロンティア育成事業」を6領域に拡充しました",
        "source": "NEDO Japan",
        "url": "https://www.nedo.go.jp/news/press/AA5_101942.html",
        "published_date": "2026-06-04",
        "matched_keywords": "量子: 量子センシング; 材料与制造: 材料",
        "suggested_type": "政策",
        "suggested_soft_hard": "软科学",
        "raw_text": "NEDOは、2040年以降の新産業の創出を目指し、フロンティア育成事業を6領域体制へ拡充しました。",
    }
