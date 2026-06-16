from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.config_loader import (
    DATA_ROOT,
    PROJECT_ROOT,
    load_keywords,
    load_profile,
    load_sources,
    master_docx_path,
    reference_docx_path,
)
from src.collectors.auto_candidates import build_auto_candidates
from src.collectors.collection import collect_articles
from src.collectors.discovery import discover_sources
from src.collectors.selected_fulltext import enrich_selected_candidates
from src.fetchers.html_fetcher import HTMLFetcher
from src.fetchers.manual_fetcher import ManualFetcher
from src.fetchers.nikkei_fetcher import (
    check_browser_env,
    nikkei_collect,
    nikkei_login,
    test_nikkei_collect,
    test_nikkei_login,
)
from src.fetchers.rss_fetcher import RSSFetcher
from src.llm.digest_generator import (
    generate_digests,
    load_final_json,
    save_final_json,
)
from src.llm.ollama_client import check_ollama_env
from src.output.excel_writer import read_reviewed_candidates, write_candidates_excel
from src.output.cumulative_exporter import export_cumulative
from src.output.word_writer import write_digest_docx
from src.reference_ingestion import REFERENCE_NEWS_PATH, ingest_reference
from src.scoring.candidate_scorer import deduplicate_candidates, score_candidate
from src.scoring.similarity_scorer import load_accepted_samples
from src.storage.database import (
    clear_run_date,
    init_db,
    load_articles,
    load_candidates,
    load_digests,
    replace_digests,
    save_articles,
    save_candidates,
)
from src.utils.dates import compact_date, iso_date
from src.utils.logger import get_logger


logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="XAutoHeadlines：每日科技要闻收集、筛选、编译与 Word 排版 MVP"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in [
        "fetch",
        "score",
        "run",
        "export-word",
        "export-cumulative",
        "discover",
        "collect",
        "auto-candidates",
        "run-collect",
        "daily-auto",
    ]:
        command = subparsers.add_parser(name)
        command.add_argument("--date", required=True, help="运行日期，格式 YYYY-MM-DD")

    subparsers.add_parser("ingest-reference")
    subparsers.add_parser("check-browser-env")
    subparsers.add_parser("check-ollama")
    subparsers.add_parser("nikkei-login")
    subparsers.add_parser("test-nikkei-login")
    subparsers.add_parser("review-app")
    subparsers.add_parser("init")
    subparsers.add_parser("doctor")

    test_nikkei_collect_parser = subparsers.add_parser("test-nikkei-collect")
    test_nikkei_collect_parser.add_argument(
        "--date", required=True, help="运行日期，格式 YYYY-MM-DD"
    )
    test_nikkei_collect_parser.add_argument(
        "--max", type=int, default=5, help="最多抓取日经候选数，默认 5"
    )

    nikkei_collect_parser = subparsers.add_parser("nikkei-collect")
    nikkei_collect_parser.add_argument(
        "--date", required=True, help="运行日期，格式 YYYY-MM-DD"
    )
    nikkei_collect_parser.add_argument(
        "--max", type=int, default=30, help="最多抓取日经候选数，默认 30"
    )

    generate = subparsers.add_parser("generate")
    generate.add_argument("--date", required=True, help="运行日期，格式 YYYY-MM-DD")
    generate.add_argument(
        "--input",
        required=True,
        help="人工审核后的 candidates Excel 路径",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    init_db()
    args = build_parser().parse_args(argv)
    run_date = iso_date(args.date) if hasattr(args, "date") else ""

    try:
        profile = load_profile()
        logger.info("Active profile: %s (%s)", profile.name, profile.profile_id)
        if args.command == "fetch":
            return command_fetch(run_date)
        if args.command == "score":
            return command_score(run_date)
        if args.command == "run":
            fetch_code = command_fetch(run_date)
            score_code = command_score(run_date)
            return fetch_code or score_code
        if args.command == "generate":
            return command_generate(run_date, Path(args.input))
        if args.command == "export-word":
            return command_export_word(run_date)
        if args.command == "export-cumulative":
            return command_export_cumulative(run_date)
        if args.command == "discover":
            return command_discover(run_date)
        if args.command == "collect":
            return command_collect(run_date)
        if args.command == "auto-candidates":
            return command_auto_candidates(run_date)
        if args.command == "run-collect":
            return command_run_collect(run_date)
        if args.command == "daily-auto":
            return command_daily_auto(run_date)
        if args.command == "test-nikkei-collect":
            return command_test_nikkei_collect(run_date, args.max)
        if args.command == "nikkei-collect":
            return command_nikkei_collect(run_date, args.max)
        if args.command == "ingest-reference":
            return command_ingest_reference()
        if args.command == "check-browser-env":
            return command_check_browser_env()
        if args.command == "check-ollama":
            return command_check_ollama()
        if args.command == "nikkei-login":
            return command_nikkei_login()
        if args.command == "test-nikkei-login":
            return command_test_nikkei_login()
        if args.command == "review-app":
            return command_review_app()
        if args.command == "init":
            return command_init()
        if args.command == "doctor":
            return command_doctor()
    except Exception as exc:
        logger.exception("Command failed: %s", exc)
        return 1
    return 0


def command_fetch(run_date: str) -> int:
    clear_run_date(run_date)
    sources = [source for source in load_sources() if source.enabled]
    fetchers = {
        "rss": RSSFetcher(),
        "html": HTMLFetcher(),
        "official": HTMLFetcher(),
        "media": HTMLFetcher(),
        "manual": ManualFetcher(),
    }
    all_articles = []
    for source in sources:
        try:
            fetcher = fetchers.get(source.source_type, HTMLFetcher())
            articles = fetcher.fetch(source)
            logger.info("Fetched %s articles from %s", len(articles), source.name)
            all_articles.extend(articles)
        except Exception as exc:
            logger.exception("Source failed and was skipped: %s | %s", source.name, exc)

    inserted = save_articles(run_date, all_articles)
    print(f"抓取完成：读取 {len(all_articles)} 条，新增入库 {inserted} 条。")
    return 0


def command_score(run_date: str) -> int:
    articles = load_articles(run_date)
    if not articles:
        print("没有可打分新闻。请先运行 fetch，或启用 manual 导入源。")
        return 0

    keywords = load_keywords()
    accepted_samples = load_accepted_samples()
    scored = [
        score_candidate(article, keywords, accepted_samples)
        for article in articles
    ]
    candidates = deduplicate_candidates(scored)
    save_candidates(run_date, candidates)
    path = write_candidates_excel(candidates, run_date)
    print(f"候选表已生成：{path}")
    return 0


def command_generate(run_date: str, reviewed_path: Path) -> int:
    if not reviewed_path.is_absolute():
        reviewed_path = PROJECT_ROOT / reviewed_path
    if not reviewed_path.exists():
        print(f"找不到人工审核文件：{reviewed_path}")
        return 1

    selected = read_reviewed_candidates(reviewed_path)
    if not selected:
        print("没有检测到 selected=采纳 的新闻。")
        return 0

    db_candidates = {item["candidate_id"]: item for item in load_candidates(run_date)}
    merged = []
    for candidate in selected:
        candidate_id = str(candidate.get("candidate_id", ""))
        enriched = db_candidates.get(candidate_id, {}).copy()
        enriched.update(candidate)
        merged.append(enriched)

    merged, fulltext_report = enrich_selected_candidates(merged)
    limited = [item for item in fulltext_report if item.get("status") == "short"]
    skipped = [item for item in fulltext_report if item.get("status") == "failed"]
    if limited:
        print(f"以下 {len(limited)} 条已选新闻正文不足，已使用候选池已有内容生成，请重点复核：")
        for item in limited:
            print(f"- {item.get('title_original')} | {item.get('warning')}")
    if skipped:
        print(f"以下 {len(skipped)} 条已选新闻完全缺少可用内容，已跳过：")
        for item in skipped:
            print(f"- {item.get('title_original')} | {item.get('warning')}")
    if not merged:
        print("没有可生成摘要的全文新闻。请检查登录状态或改用手动导入正文。")
        return 1

    quality_skipped: list[dict[str, Any]] = []
    digests = generate_digests(merged, run_date, quality_report=quality_skipped)
    if quality_skipped:
        print(f"以下 {len(quality_skipped)} 条摘要未通过质量检查，已跳过：")
        for item in quality_skipped:
            print(f"- {item.get('title_original')} | {item.get('issue')}")
    if not digests:
        print("所有已选新闻的摘要均未通过质量检查，未生成摘要 JSON。")
        return 1
    replace_digests(run_date, digests)
    path = save_final_json(digests, run_date)
    print(f"摘要 JSON 已生成：{path}")
    return 0


def command_export_word(run_date: str) -> int:
    digests = load_final_json(run_date) or load_digests(run_date)
    if not digests:
        print("没有可导出的摘要。请先运行 generate。")
        return 0
    path = write_digest_docx(digests, run_date)
    print(f"Word 文档已生成：{path}")
    return 0


def command_export_cumulative(run_date: str) -> int:
    json_path, docx_path, blocks = export_cumulative(run_date)
    count = sum(len(block.get("items", [])) for block in blocks)
    print(f"累计 JSON 已生成：{json_path}")
    print(f"累计 Word 已生成：{docx_path}")
    print(f"累计日期块 {len(blocks)} 个，要闻 {count} 条。")
    return 0


def command_ingest_reference() -> int:
    news_path, keywords_path, stats_path = ingest_reference()
    print(f"参考新闻库已生成：{news_path}")
    print(f"参考关键词已生成：{keywords_path}")
    print(f"参考统计已生成：{stats_path}")
    return 0


def command_discover(run_date: str) -> int:
    rows = discover_sources(run_date)
    print(f"发现完成：{len(rows)} 条候选链接。")
    return 0


def command_collect(run_date: str) -> int:
    rows = collect_articles(run_date)
    print(f"采集完成：{len(rows)} 条文章记录。")
    return 0


def command_auto_candidates(run_date: str) -> int:
    candidates = build_auto_candidates(run_date)
    print(f"自动候选池已生成：{len(candidates)} 条。")
    print(
        f"候选表：{DATA_ROOT / 'output' / f'candidates_{compact_date(run_date)}.xlsx'}"
    )
    return 0


def command_run_collect(run_date: str) -> int:
    command_discover(run_date)
    command_collect(run_date)
    return command_auto_candidates(run_date)


def command_daily_auto(run_date: str) -> int:
    clear_run_date(run_date)
    if REFERENCE_NEWS_PATH.exists():
        logger.info("Reference library exists, skipping ingest-reference.")
    else:
        try:
            ingest_reference()
        except Exception as exc:
            logger.exception("Reference ingestion skipped after failure: %s", exc)
    return command_run_collect(run_date)


def command_nikkei_login() -> int:
    result = nikkei_login()
    _print_browser_result(result)
    return 0 if result.get("ok", True) is not False else 0


def command_test_nikkei_login() -> int:
    result = test_nikkei_login()
    print(f"logged_in: {result.get('logged_in')}")
    print(f"current_url: {result.get('current_url')}")
    print(f"page_title: {result.get('page_title')}")
    print(f"visible_account_hint: {result.get('visible_account_hint', '')}")
    print(f"profile_path: {result.get('profile_path', '')}")
    if result.get("warning"):
        print(f"warning: {result.get('warning')}")
    return 0


def command_test_nikkei_collect(run_date: str, max_articles: int) -> int:
    jsonl_path, excel_path, rows = test_nikkei_collect(run_date, max_articles)
    print(f"Nikkei 测试 JSONL：{jsonl_path}")
    print(f"Nikkei 测试候选表：{excel_path}")
    print(f"Nikkei 测试记录：{len(rows)} 条")
    return 0


def command_nikkei_collect(run_date: str, max_articles: int) -> int:
    jsonl_path, excel_path, rows = nikkei_collect(
        run_date, max_articles=max_articles, test_mode=False
    )
    print(f"Nikkei JSONL：{jsonl_path}")
    print(f"Nikkei 候选表：{excel_path}")
    print(f"Nikkei 候选记录：{len(rows)} 条")
    return 0


def command_check_browser_env() -> int:
    result = check_browser_env()
    print(f"playwright_installed: {result.get('playwright_installed')}")
    print(f"chromium_installed: {result.get('chromium_installed')}")
    print(f"profile_exists: {result.get('profile_exists')}")
    print(f"profile_path: {result.get('profile_path')}")
    if result.get("warning"):
        print(f"warning: {result.get('warning')}")
    if not result.get("playwright_installed"):
        print("install_playwright:")
        print(result.get("install_playwright_command"))
    if not result.get("chromium_installed"):
        print("install_chromium:")
        print(result.get("install_chromium_command"))
    return 0


def command_check_ollama() -> int:
    result = check_ollama_env()
    print(f"ollama_command_exists: {result.get('ollama_command_exists')}")
    print(f"ollama_running: {result.get('ollama_running')}")
    print(f"model: {result.get('model')}")
    print(f"model_installed: {result.get('model_installed')}")
    print(f"base_url: {result.get('base_url')}")
    if result.get("ollama_version"):
        print(f"ollama_version: {result.get('ollama_version')}")
    installed = result.get("installed_models") or []
    if installed:
        print("installed_models:")
        for model in installed:
            print(f"- {model}")
    if result.get("warning"):
        print(f"warning: {result.get('warning')}")
    if result.get("update_ollama_url"):
        print(f"update_ollama_url: {result.get('update_ollama_url')}")
    if not result.get("ollama_command_exists"):
        print("install_ollama:")
        print(result.get("install_ollama_command"))
    if result.get("ollama_command_exists") and not result.get("ollama_running"):
        print("start_ollama:")
        print(result.get("start_ollama_command"))
    if result.get("ollama_running") and not result.get("model_installed"):
        print("pull_model:")
        print(result.get("pull_model_command"))
    return 0


def command_review_app() -> int:
    try:
        import streamlit  # noqa: F401
    except Exception:
        print("Streamlit 未安装。请先运行：")
        print("python -m pip install -r requirements.txt")
        return 1

    app_path = PROJECT_ROOT / "src" / "ui" / "review_app.py"
    return subprocess.call(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(app_path),
            "--server.headless=false",
            "--server.address=127.0.0.1",
            "--server.showEmailPrompt=false",
            "--browser.gatherUsageStats=false",
        ],
        cwd=str(PROJECT_ROOT),
    )


def command_init() -> int:
    for name in ["input", "output", "processed", "raw", "reference", "settings"]:
        (DATA_ROOT / name).mkdir(parents=True, exist_ok=True)
    env_path = PROJECT_ROOT / ".env"
    example_path = PROJECT_ROOT / ".env.example"
    if not env_path.exists() and example_path.exists():
        env_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"已创建环境配置：{env_path}")
    print(f"数据目录已准备：{DATA_ROOT}")
    print("下一步：编辑 .env，配置 OpenAI 或 Ollama，然后运行 xautoheadlines doctor。")
    return 0


def command_doctor() -> int:
    profile = load_profile()
    checks = {
        "profile": profile.name,
        "sources_file": profile.sources_file.exists(),
        "keywords_file": profile.keywords_file.exists(),
        "prompt_file": profile.prompt_file.exists(),
        "data_root": str(DATA_ROOT),
        "master_docx_exists": master_docx_path().exists(),
        "reference_docx_exists": reference_docx_path().exists(),
    }
    for key, value in checks.items():
        print(f"{key}: {value}")
    missing = [
        key
        for key in ["sources_file", "keywords_file", "prompt_file"]
        if not checks[key]
    ]
    if missing:
        print("missing: " + ", ".join(missing))
        return 1
    print(f"enabled_sources: {sum(1 for source in load_sources() if source.enabled)}")
    print(f"keywords: {len(load_keywords())}")
    return 0


def _print_browser_result(result: dict) -> None:
    for key in [
        "profile_path",
        "current_url",
        "page_title",
        "login_status",
        "logged_in",
        "visible_account_hint",
        "warning",
    ]:
        if key in result:
            print(f"{key}: {result.get(key)}")


if __name__ == "__main__":
    sys.exit(main())
