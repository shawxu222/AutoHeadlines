from __future__ import annotations

from pathlib import Path

from src.collectors import discovery
from src.config_loader import NewsSource
from src.utils.jsonl import read_jsonl, write_jsonl


def test_relevant_link_rejects_rss_feed_url() -> None:
    assert not discovery._is_relevant_link(  # noqa: SLF001
        "https://www.jst.go.jp/rss/press.xml",
        "RSS購読",
    )


def test_configured_include_patterns_override_generic_link_heuristics() -> None:
    source = NewsSource(
        name="Example",
        country_region="US",
        language="en",
        section_url="https://example.com/latest",
        source_type="html",
        requires_login=False,
        priority=3,
        include_url_patterns=[r"/story/\d+$"],
        exclude_url_patterns=[r"/sports/"],
    )

    assert discovery._is_relevant_for_source(  # noqa: SLF001
        source,
        "https://example.com/story/123",
        "Short",
    )
    assert not discovery._is_relevant_for_source(  # noqa: SLF001
        source,
        "https://example.com/sports/story/123",
        "Short",
    )


def test_new_official_sources_only_keep_concrete_update_pages() -> None:
    assert discovery._is_source_specific_relevant_link(  # noqa: SLF001
        "AMED Japan",
        "https://www.amed.go.jp/news/release_20260609.html",
        "研究成果",
    )
    assert not discovery._is_source_specific_relevant_link(  # noqa: SLF001
        "AMED Japan",
        "https://www.amed.go.jp/program/list/index06.html",
        "シーズ開発・基礎研究プロジェクト",
    )
    assert discovery._is_source_specific_relevant_link(  # noqa: SLF001
        "NICT Japan",
        "https://www.nict.go.jp/press/2026/05/27-1.html",
        "研究成果",
    )
    assert not discovery._is_source_specific_relevant_link(  # noqa: SLF001
        "QST Japan",
        "https://www.qst.go.jp/site/research/",
        "研究開発体制",
    )
    assert discovery._is_source_specific_relevant_link(  # noqa: SLF001
        "JAXA Japan",
        "https://www.jaxa.jp/press/2026/06/20260611-1_j.html",
        "選定結果",
    )


def test_jaxa_discovery_uses_run_year_index() -> None:
    source = NewsSource(
        name="JAXA Japan",
        country_region="Japan",
        language="ja",
        section_url="https://www.jaxa.jp/press/index_j.html",
        source_type="official",
        requires_login=False,
        priority=5,
        base_url="https://www.jaxa.jp/",
        discovery_urls=["https://www.jaxa.jp/press/index_j.html"],
    )

    urls = discovery._source_discovery_urls(source, "2027-01-05")  # noqa: SLF001

    assert urls[0] == "https://www.jaxa.jp/press/2027/index_j.html"


def test_discovery_urls_support_run_date_templates() -> None:
    source = NewsSource(
        name="Example",
        country_region="Global",
        language="en",
        section_url="https://example.com/news",
        source_type="official",
        requires_login=False,
        priority=3,
        discovery_urls=[
            "https://example.com/archive/{year}/",
            "https://example.com/daily/{run_date}/",
        ],
    )

    assert discovery._source_discovery_urls(source, "2027-01-05") == [  # noqa: SLF001
        "https://example.com/archive/2027/",
        "https://example.com/daily/2027-01-05/",
    ]


def test_new_official_source_dates_are_parsed_from_urls() -> None:
    assert discovery._date_from_url(  # noqa: SLF001
        "https://www.amed.go.jp/news/release_20260609.html"
    ) == "2026-06-09"
    assert discovery._date_from_url(  # noqa: SLF001
        "https://www.nict.go.jp/press/2026/05/27-1.html"
    ) == "2026-05-27"
    assert discovery._date_from_url(  # noqa: SLF001
        "https://www.qst.go.jp/site/press/20260611.html"
    ) == "2026-06-11"
    assert discovery._date_from_url(  # noqa: SLF001
        "https://www.jaxa.jp/press/2026/06/20260611-1_j.html"
    ) == "2026-06-11"


def test_cabinet_program_sources_only_keep_strategy_updates() -> None:
    assert discovery._is_source_specific_relevant_link(  # noqa: SLF001
        "Cabinet Office K Program",
        "https://www8.cao.go.jp/cstp/tougosenryaku/22kai/22kai.html",
        "研究開発ビジョンの一部改定を決定",
    )
    assert not discovery._is_source_specific_relevant_link(  # noqa: SLF001
        "Cabinet Office Moonshot",
        "https://www8.cao.go.jp/cstp/moonshot/sub1.html",
        "ムーンショット型研究開発制度の概要",
    )
    assert not discovery._is_source_specific_relevant_link(  # noqa: SLF001
        "Cabinet Office SIP",
        "https://www8.cao.go.jp/cstp/gaiyo/sip/sip3rd_list.html",
        "SIP第3期課題一覧",
    )


def test_new_official_sources_use_tight_scan_limits() -> None:
    assert discovery._source_scan_limit("AMED Japan", 10) == 10  # noqa: SLF001
    assert discovery._source_scan_limit("JAXA Japan", 10) == 10  # noqa: SLF001
    assert discovery._source_scan_limit("Yonhap News", 10) == 30  # noqa: SLF001


def test_discover_sources_preserves_previous_source_rows_after_empty_discovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    previous = [
        {
            "source": "Yonhap News",
            "url": "https://www.yna.co.kr/view/AKR20260604074000003",
            "title_original": "대한전선, 영국 스코틀랜드서 사업 수주",
        },
        {
            "source": "JST Japan",
            "url": "https://www.jst.go.jp/rss/press.xml",
            "title_original": "RSS購読",
        },
    ]
    write_jsonl(
        tmp_path / "data" / "processed" / "discovered_20260604.jsonl",
        previous,
    )
    source = NewsSource(
        name="Yonhap News",
        country_region="Korea",
        language="ko",
        section_url="https://www.yna.co.kr/industry/technology-science",
        source_type="html",
        requires_login=False,
        priority=3,
        base_url="https://www.yna.co.kr",
        discovery_urls=["https://www.yna.co.kr/industry/technology-science"],
    )

    monkeypatch.setattr(discovery, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(discovery, "load_known_urls", lambda exclude_run_date=None: set())
    monkeypatch.setattr(discovery, "load_sources", lambda: [source])
    monkeypatch.setattr(discovery, "discover_source", lambda *args, **kwargs: [])

    rows = discovery.discover_sources("2026-06-04")
    written = read_jsonl(tmp_path / "data" / "processed" / "discovered_20260604.jsonl")

    assert [row["url"] for row in rows] == [
        "https://www.yna.co.kr/view/AKR20260604074000003"
    ]
    assert written == rows
