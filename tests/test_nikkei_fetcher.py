from __future__ import annotations

from src.fetchers.nikkei_fetcher import (
    NIKKEI_PRIORITY_SECTIONS,
    _nikkei_discovery_page_is_relevant,
    _nikkei_section_label,
    _goto_with_retries,
    _nikkei_status_is_hard_blocker,
)


class FakePage:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.goto_calls = 0
        self.waits: list[int] = []

    def goto(self, url: str, wait_until: str, timeout: int) -> None:
        self.goto_calls += 1
        if self.goto_calls <= self.failures:
            raise RuntimeError("Page.goto: net::ERR_CONNECTION_RESET")

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.waits.append(milliseconds)


def test_goto_with_retries_recovers_after_transient_failure() -> None:
    page = FakePage(failures=1)

    ok, warning = _goto_with_retries(
        page,
        "https://www.nikkei.com/",
        attempts=3,
        retry_wait_ms=10,
    )

    assert ok is True
    assert warning == ""
    assert page.goto_calls == 2
    assert page.waits == [10]


def test_goto_with_retries_reports_final_failure() -> None:
    page = FakePage(failures=3)

    ok, warning = _goto_with_retries(
        page,
        "https://www.nikkei.com/",
        attempts=3,
        retry_wait_ms=10,
        label="Nikkei home",
    )

    assert ok is False
    assert page.goto_calls == 3
    assert page.waits == [10, 20]
    assert "Nikkei home" in warning
    assert "已重试 3 次" in warning


def test_transient_login_check_failure_does_not_block_collection() -> None:
    status = {
        "logged_in": "unknown",
        "warning": "无法使用日经专用 profile 打开浏览器：Page.goto: net::ERR_CONNECTION_RESET",
    }

    assert _nikkei_status_is_hard_blocker(status) is False


def test_missing_browser_environment_blocks_collection() -> None:
    assert _nikkei_status_is_hard_blocker({"warning": "Playwright 未安装"}) is True
    assert _nikkei_status_is_hard_blocker({"warning": "日经专用 profile 不存在"}) is True


def test_stale_theme_ids_are_not_priority_sections() -> None:
    priority_urls = {url for _, url in NIKKEI_PRIORITY_SECTIONS}

    assert "https://www.nikkei.com/theme/?dw=23022100" not in priority_urls
    assert "https://www.nikkei.com/theme/?dw=23062000" not in priority_urls
    assert _nikkei_section_label("Nikkei", "https://www.nikkei.com/theme/?dw=23022100") == "Nikkei"


def test_off_topic_nikkei_topic_pages_are_skipped() -> None:
    assert (
        _nikkei_discovery_page_is_relevant(
            "Tech/AI",
            "https://www.nikkei.com/topics/23022100",
            "児童手当の最新ニュース 所得制限の是非は？ - 日本経済新聞",
        )
        is False
    )


def test_relevant_nikkei_topic_pages_are_kept() -> None:
    assert (
        _nikkei_discovery_page_is_relevant(
            "Tech/AI",
            "https://www.nikkei.com/topics/ai",
            "生成AIの最新ニュース - 日本経済新聞",
        )
        is True
    )
