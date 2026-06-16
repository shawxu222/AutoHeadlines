from __future__ import annotations

import html
import json
import os
import platform
import queue
import re
import subprocess
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st
import yaml
from dotenv import load_dotenv
from PIL import Image

from src.collectors.auto_candidates import build_auto_candidates
from src.collectors.collection import collect_articles
from src.collectors.discovery import discover_sources
from src.collectors.reported_history import load_reported_urls, normalize_reported_url
from src.collectors.selected_fulltext import enrich_selected_candidates
from src.collectors.source_diagnostics import diagnose_source
from src.collectors.url_filters import is_feed_url
from src.config_loader import (
    DATA_ROOT,
    PRIVATE_SETTINGS_PATH,
    PROJECT_ROOT,
    active_sources_path,
    load_keywords,
    load_sources,
    master_docx_path,
    reference_docx_path,
)
from src.fetchers.nikkei_fetcher import nikkei_collect, test_nikkei_login
from src.llm.digest_generator import final_json_path, generate_digests, save_final_json
from src.llm.config_assistant import (
    ask_configuration_assistant,
    source_config_from_suggestion,
)
from src.llm.ollama_client import check_ollama_env
from src.llm.title_translator import translate_korean_candidate_titles
from src.output.analytics import (
    analytics_export_path,
    analytics_frame,
    build_analytics_records,
    daily_counts_frame,
    date_range_for_period,
    distribution_frame,
    export_analytics_excel,
    filter_records_by_date,
    keyword_counts,
    summary_metrics,
    target_metrics_for_period,
)
from src.output.acceptance_marker import (
    load_acceptance_entries,
    sync_acceptance_highlights,
)
from src.output.cumulative_exporter import export_cumulative, merge_final_json_into_cumulative
from src.output.excel_writer import is_selected
from src.output.master_word_updater import update_master_digest_docx
from src.output.word_writer import write_digest_docx
from src.reference_ingestion import ingest_reference, load_reference_samples
from src.scoring.candidate_scorer import candidate_id_for, is_excluded_topic, score_candidate
from src.storage.database import clear_run_date, init_db, replace_digests
from src.utils.date_window import (
    append_window_warning,
    collection_window_label,
    is_in_collection_window,
)
from src.utils.dates import compact_date, iso_date


load_dotenv(PROJECT_ROOT / ".env")
init_db()

OUTPUT_DIR = DATA_ROOT / "output"
SETTINGS_PATH = PRIVATE_SETTINGS_PATH
SOURCES_PATH = active_sources_path()
ENV_PATH = PROJECT_ROOT / ".env"
APP_ICON_PATH = PROJECT_ROOT / "assets" / "icons" / "XAutoHeadlines.png"
MIN_CANDIDATE_TEXT_CHARS = 250
MODEL_OPTIONS = [
    "gpt5.5thinking",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.2",
]
PROVIDER_LABELS = {
    "openai": "OpenAI API",
    "ollama": "本地 Ollama",
}
OLLAMA_THINK_MODE_LABELS = {
    "fast": "快速模式（关闭思考，速度较快）",
    "deep": "深度思考模式（开启思考，速度较慢）",
    "auto": "模型默认（不强制开关思考）",
}
DISPLAY_COLUMNS = [
    "selected",
    "score",
    "title_original",
    "title_translated_candidate",
    "published_date",
    "source",
    "source_section",
    "recommended_reason",
    "matched_keywords",
    "extraction_warning",
    "url",
]
WORKSPACE_VIEWS = ["今日工作台", "AI 配置助手", "被采纳标记", "数据统计"]


def main() -> None:
    st.set_page_config(
        page_title="XAutoHeadlines",
        page_icon=Image.open(APP_ICON_PATH),
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_app_styles()
    _render_app_header()
    pending_view = st.session_state.pop("pending_workspace_view", None)
    if pending_view in WORKSPACE_VIEWS:
        st.session_state["workspace_view"] = pending_view
    if "workspace_view" not in st.session_state:
        st.session_state["workspace_view"] = WORKSPACE_VIEWS[0]
    workspace_view = st.segmented_control(
        "工作区",
        WORKSPACE_VIEWS,
        key="workspace_view",
        label_visibility="collapsed",
    )

    with st.sidebar:
        st.header("设置")
        st.caption(_api_status())
        _master_word_settings_panel()

        st.divider()
        _model_settings_panel()

        st.divider()
        _candidate_pool_panel()

        st.divider()
        _reference_panel()

    if workspace_view == "AI 配置助手":
        _configuration_assistant_panel()
        return

    with st.container(key="workflow_toolbar"):
        date_col, window_col, selected_col = st.columns([1, 2.4, 1])
        with date_col:
            run_date = st.date_input(
                "运行日期",
                value=date.today(),
                format="YYYY-MM-DD",
                key="workflow_run_date",
            )
        run_date_text = iso_date(run_date)
        compact = compact_date(run_date_text)
        toolbar_candidate_path = _active_candidate_file(compact)
        toolbar_frame = _cached_candidate_frame(toolbar_candidate_path)
        job_running = _has_running_job()

        with window_col:
            st.markdown("**收集范围**")
            st.caption(collection_window_label(run_date_text))
        with selected_col:
            _selected_count_metric(toolbar_candidate_path)

        collect_col, save_col, word_col, folder_col = st.columns([1.2, 1, 1.4, 1])
        with collect_col:
            if st.button(
                "生成今日候选池",
                type="primary",
                width="stretch",
                disabled=job_running,
            ):
                _start_collect_job(run_date_text, compact)
                st.rerun()
        with save_col:
            if st.button(
                "保存选择",
                width="stretch",
                disabled=toolbar_frame is None,
            ):
                latest_frame = _cached_candidate_frame(toolbar_candidate_path)
                if latest_frame is None:
                    st.warning("还没有可保存的候选表。")
                    st.stop()
                reviewed_path = _save_reviewed_candidates(latest_frame, run_date_text)
                st.success(f"已保存：{reviewed_path.name}")
        with word_col:
            if st.button(
                "生成并追加总 Word",
                type="primary",
                width="stretch",
                disabled=job_running or toolbar_frame is None,
            ):
                latest_frame = _cached_candidate_frame(toolbar_candidate_path)
                if latest_frame is None:
                    st.warning("还没有可写入 Word 的候选表。")
                    st.stop()
                latest_selected_count = _selected_count(latest_frame)
                if latest_selected_count == 0:
                    st.warning("请先至少勾选一条候选新闻。")
                    st.stop()
                reviewed_path = _save_reviewed_candidates(latest_frame, run_date_text)
                settings = _load_user_settings()
                _start_word_job(
                    run_date_text,
                    reviewed_path,
                    _master_docx_path_from_settings(settings),
                    _save_daily_word_from_settings(settings),
                )
                st.rerun()
        with folder_col:
            if st.button("打开输出文件夹", width="stretch"):
                _open_in_finder(OUTPUT_DIR)

    loading_area = st.empty()
    _render_active_job(loading_area)

    if workspace_view == "数据统计":
        _analytics_dashboard_panel(run_date_text)
        return

    if workspace_view == "被采纳标记":
        _acceptance_marker_panel()
        return

    candidate_path = _choose_candidate_file(compact)
    if not candidate_path:
        st.info("还没有找到当天候选表。请点击页面顶部的“生成今日候选池”。")
        return

    st.subheader("候选新闻")
    st.caption(candidate_path.name)
    frame = _cached_candidate_frame(candidate_path)
    if frame is None:
        frame = _load_candidates(candidate_path)
    st.session_state["candidate_raw_text_map"] = {
        str(row.get("candidate_id", "")): str(row.get("raw_text", ""))
        for _, row in frame.iterrows()
        if row.get("raw_text", "")
    }
    _cache_candidate_frame(candidate_path, frame)
    _candidate_workspace_fragment(str(candidate_path), run_date_text)


def _inject_app_styles() -> None:
    st.markdown(
        """
<style>
  .block-container {
    padding-top: 2rem;
    padding-bottom: 3rem;
  }
  .st-key-workflow_toolbar {
    position: sticky;
    top: 2.75rem;
    z-index: 20;
    padding: 0.85rem 1rem 0.95rem;
    margin: 0.75rem 0 1rem;
    background: color-mix(in srgb, var(--background-color) 96%, #ffffff 4%);
    border: 1px solid color-mix(in srgb, var(--text-color) 20%, transparent);
    border-radius: 8px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.16);
  }
  .st-key-workflow_toolbar [data-testid="stMetricValue"] {
    font-size: 1.7rem;
  }
  .st-key-candidate_filters {
    padding: 0.65rem 0.8rem 0.2rem;
    border-top: 1px solid color-mix(in srgb, var(--text-color) 18%, transparent);
    border-bottom: 1px solid color-mix(in srgb, var(--text-color) 18%, transparent);
    margin-bottom: 0.75rem;
  }
  div[data-testid="stDataFrame"] {
    border-radius: 6px;
  }
</style>
        """,
        unsafe_allow_html=True,
    )


def _render_app_header() -> None:
    icon_col, title_col = st.columns([0.075, 0.925], vertical_alignment="center")
    with icon_col:
        st.image(str(APP_ICON_PATH), width=72)
    with title_col:
        st.title("XAutoHeadlines")
        st.caption("科技要闻候选审核、自动归纳与统计工作台")


def _api_status() -> str:
    provider = os.getenv("LLM_PROVIDER", "openai").strip().lower() or "openai"
    if provider == "ollama":
        base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip()
        model = os.getenv("OLLAMA_MODEL", "qwen3:8b").strip() or "qwen3:8b"
        think_mode = _ollama_think_mode()
        mode_label = OLLAMA_THINK_MODE_LABELS.get(think_mode, OLLAMA_THINK_MODE_LABELS["fast"])
        status = check_ollama_env(base_url, model)
        if status.get("ollama_running") and status.get("model_installed"):
            return f"本地 Ollama：已就绪，将使用 {model} / {mode_label} 生成正式摘要。"
        if status.get("ollama_running"):
            return f"本地 Ollama：服务已运行，但还没下载 {model}，暂时不能生成正式摘要。"
        return "本地 Ollama：服务未运行，暂时不能生成正式摘要。请先打开 Ollama 或运行 ollama serve。"
    has_key = bool(os.getenv("OPENAI_API_KEY", "").strip())
    model = os.getenv("OPENAI_MODEL", "").strip()
    effort = os.getenv("OPENAI_REASONING_EFFORT", "high").strip()
    has_model = bool(model)
    if has_key and has_model:
        return f"OpenAI API：已配置，将使用 {model}（reasoning={effort}）生成正式摘要。"
    return "OpenAI API：未完整配置，生成摘要前需要完成模型设置。"


def _ollama_think_mode() -> str:
    mode = os.getenv("OLLAMA_THINK_MODE", "fast").strip().lower() or "fast"
    if mode in OLLAMA_THINK_MODE_LABELS:
        return mode
    if mode in {"quick", "no_think", "no-think", "false", "0"}:
        return "fast"
    if mode in {"thinking", "think", "true", "1"}:
        return "deep"
    return "fast"


def _model_settings_panel() -> None:
    st.subheader("模型")
    provider = os.getenv("LLM_PROVIDER", "openai").strip().lower() or "openai"
    if provider not in PROVIDER_LABELS:
        provider = "openai"
    has_key = bool(os.getenv("OPENAI_API_KEY", "").strip())
    model = os.getenv("OPENAI_MODEL", "gpt5.5thinking").strip() or "gpt5.5thinking"
    effort = os.getenv("OPENAI_REASONING_EFFORT", "high").strip() or "high"
    ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip()
    ollama_model = os.getenv("OLLAMA_MODEL", "qwen3:8b").strip() or "qwen3:8b"
    ollama_think_mode = _ollama_think_mode()
    display_model = model if model in MODEL_OPTIONS else "自定义"
    key_status = "API Key 已配置" if has_key else "API Key 未配置"
    if provider == "ollama":
        mode_label = OLLAMA_THINK_MODE_LABELS.get(
            ollama_think_mode, OLLAMA_THINK_MODE_LABELS["fast"]
        )
        st.caption(f"当前：本地 Ollama / {ollama_model} / {mode_label}")
    else:
        st.caption(f"{key_status}；当前模型：{model} / reasoning={effort}")
    with st.expander("设置 API Key", expanded=(provider == "openai" and not has_key)):
        st.caption("只有选择 OpenAI API 时才需要填写；本地 Ollama 不需要 API Key。")
        api_key = st.text_input(
            "OpenAI API Key",
            type="password",
            placeholder="sk-...",
            help="只保存在本机 .env 文件中；不会显示在页面上，也不会写入日志。",
        )
        col_key_save, col_key_clear = st.columns(2)
        with col_key_save:
            if st.button("保存 API Key", width="stretch"):
                if not api_key.strip():
                    st.warning("请先粘贴 API Key。")
                    return
                _save_env_values({"OPENAI_API_KEY": api_key.strip()})
                os.environ["OPENAI_API_KEY"] = api_key.strip()
                st.success("API Key 已保存。重新生成 Word 时会调用真实模型。")
                st.rerun()
        with col_key_clear:
            if has_key and st.button("清除 Key", width="stretch"):
                _save_env_values({"OPENAI_API_KEY": ""})
                os.environ["OPENAI_API_KEY"] = ""
                st.success("API Key 已清除。")
                st.rerun()

    with st.expander("更换模型", expanded=False):
        provider_label = st.selectbox(
            "模型来源",
            list(PROVIDER_LABELS.values()),
            index=list(PROVIDER_LABELS).index(provider),
        )
        selected_provider = next(
            key for key, value in PROVIDER_LABELS.items() if value == provider_label
        )
        selected = "自定义"
        custom_model = ""
        selected_effort = effort
        selected_ollama_model = ollama_model
        selected_ollama_base_url = ollama_base_url
        selected_ollama_think_mode = ollama_think_mode
        if selected_provider == "openai":
            selected = st.selectbox(
                "OpenAI 摘要编译模型",
                MODEL_OPTIONS + ["自定义"],
                index=(MODEL_OPTIONS + ["自定义"]).index(display_model),
            )
            if selected == "自定义":
                custom_model = st.text_input("自定义模型名", value=model)
            selected_effort = st.selectbox(
                "思考强度",
                ["high", "medium", "low"],
                index=["high", "medium", "low"].index(
                    effort if effort in {"high", "medium", "low"} else "high"
                ),
            )
        else:
            selected_ollama_model = st.text_input(
                "Ollama 模型名",
                value=ollama_model,
                help="本地推荐先用 qwen3:8b。请先在终端执行 ollama pull qwen3:8b。",
            )
            selected_ollama_base_url = st.text_input(
                "Ollama 地址",
                value=ollama_base_url,
                help="默认是本机地址 http://127.0.0.1:11434。",
            )
            selected_ollama_think_label = st.selectbox(
                "本地模型模式",
                list(OLLAMA_THINK_MODE_LABELS.values()),
                index=list(OLLAMA_THINK_MODE_LABELS).index(ollama_think_mode),
                help="快速模式适合批量处理；深度思考模式适合最终编译或复杂判断，但会更慢。",
            )
            selected_ollama_think_mode = next(
                key
                for key, value in OLLAMA_THINK_MODE_LABELS.items()
                if value == selected_ollama_think_label
            )
        if st.button("保存模型设置", width="stretch"):
            if selected_provider == "openai":
                new_model = custom_model.strip() if selected == "自定义" else selected
                if not new_model:
                    st.warning("请填写模型名。")
                    return
                _save_env_values(
                    {
                        "LLM_PROVIDER": "openai",
                        "OPENAI_MODEL": new_model,
                        "OPENAI_REASONING_EFFORT": selected_effort,
                        "OPENAI_USE_RESPONSES": "true",
                    }
                )
                os.environ["LLM_PROVIDER"] = "openai"
                os.environ["OPENAI_MODEL"] = new_model
                os.environ["OPENAI_REASONING_EFFORT"] = selected_effort
                os.environ["OPENAI_USE_RESPONSES"] = "true"
            else:
                new_ollama_model = selected_ollama_model.strip()
                new_ollama_base_url = selected_ollama_base_url.strip()
                if not new_ollama_model or not new_ollama_base_url:
                    st.warning("请填写 Ollama 模型名和地址。")
                    return
                _save_env_values(
                    {
                        "LLM_PROVIDER": "ollama",
                        "OLLAMA_BASE_URL": new_ollama_base_url,
                        "OLLAMA_MODEL": new_ollama_model,
                        "OLLAMA_THINK_MODE": selected_ollama_think_mode,
                    }
                )
                os.environ["LLM_PROVIDER"] = "ollama"
                os.environ["OLLAMA_BASE_URL"] = new_ollama_base_url
                os.environ["OLLAMA_MODEL"] = new_ollama_model
                os.environ["OLLAMA_THINK_MODE"] = selected_ollama_think_mode
            st.success("模型设置已保存。重新生成 Word 时会使用新的模型来源。")
            st.rerun()


def _email_settings_panel() -> None:
    st.subheader("通知邮箱")
    settings = _load_user_settings()
    saved_email = str(settings.get("notification_email", "")).strip()

    if "changing_email" not in st.session_state:
        st.session_state.changing_email = False

    if saved_email and not st.session_state.changing_email:
        st.write(f"当前：{_mask_email(saved_email)}")
        if st.button("更换邮箱", width="stretch"):
            st.session_state.changing_email = True
            st.rerun()
        return

    email = st.text_input(
        "邮箱地址",
        value=saved_email,
        placeholder="例如 name@example.com",
        help="该邮箱会保存在本机数据目录。当前版本先用于记住通知地址，后续邮件推送会使用它。",
    )
    col_save, col_cancel = st.columns(2)
    with col_save:
        if st.button("保存邮箱", width="stretch"):
            if email.strip() and not _valid_email(email.strip()):
                st.warning("邮箱格式看起来不对，请检查一下。")
                return
            settings["notification_email"] = email.strip()
            _save_user_settings(settings)
            st.session_state.changing_email = False
            st.success("邮箱已保存。")
            st.rerun()
    with col_cancel:
        if saved_email and st.button("取消", width="stretch"):
            st.session_state.changing_email = False
            st.rerun()


def _master_word_settings_panel() -> None:
    settings = _load_user_settings()
    current_path = str(_master_docx_path_from_settings(settings))
    save_daily_word = _save_daily_word_from_settings(settings)

    with st.expander("总 Word 设置", expanded=False):
        path = Path(current_path).expanduser()
        if path.exists():
            st.caption(f"当前总 Word：已找到 {path.name}")
        else:
            st.caption("当前总 Word：文件还不存在，首次生成时会自动创建。")

        master_docx_path = st.text_input(
            "总 Word 文件路径",
            value=current_path,
            help="每天生成的要闻会追加到这个 Word。若当天区块已存在，会自动替换。",
        )
        save_single_day_docx = st.checkbox(
            "同时保存今日单独 Word",
            value=save_daily_word,
            help="默认关闭。开启后仍会额外生成 final_digest_YYYYMMDD.docx。",
        )
        if st.button("保存总 Word 设置", width="stretch"):
            if not master_docx_path.strip():
                st.warning("请填写总 Word 文件路径。")
                return
            settings["master_docx_path"] = master_docx_path.strip()
            settings["save_daily_word"] = bool(save_single_day_docx)
            _save_user_settings(settings)
            st.success("总 Word 设置已保存。")
            st.rerun()


def _master_docx_path_from_settings(settings: dict[str, Any]) -> Path:
    configured = str(settings.get("master_docx_path", "")).strip()
    if configured:
        return Path(configured).expanduser()
    return _default_master_docx_path()


def _analytics_dashboard_panel(run_date_text: str) -> None:
    st.subheader("统计 Dashboard")
    settings = _load_user_settings()
    master_path = _master_docx_path_from_settings(settings)
    st.caption(f"总 Word：{master_path}")
    if not master_path.exists():
        st.warning("当前总 Word 路径不存在。Dashboard 会只使用已有 JSON 数据，无法读取黄色高亮采纳状态。")

    records = build_analytics_records(master_path if master_path.exists() else None)
    if not records:
        st.info("还没有可统计的数据。请先生成并追加总 Word。")
        if st.button("关闭统计 Dashboard", width="stretch"):
            _go_to_workbench()
            st.rerun()
        return

    col_period, col_anchor, col_close = st.columns([1, 1, 1])
    with col_period:
        period = st.selectbox("统计周期", ["周度", "月度", "自定义"], index=0)
    with col_anchor:
        anchor_date = st.date_input(
            "基准日期",
            value=date.fromisoformat(run_date_text),
            format="YYYY-MM-DD",
            key="analytics_anchor_date",
        )
    with col_close:
        st.write("")
        st.write("")
        if st.button("关闭 Dashboard", width="stretch"):
            _go_to_workbench()
            st.rerun()

    custom_start = custom_end = None
    if period == "自定义":
        col_start, col_end = st.columns(2)
        with col_start:
            custom_start = st.date_input(
                "开始日期",
                value=anchor_date,
                format="YYYY-MM-DD",
                key="analytics_custom_start",
            ).isoformat()
        with col_end:
            custom_end = st.date_input(
                "结束日期",
                value=anchor_date,
                format="YYYY-MM-DD",
                key="analytics_custom_end",
            ).isoformat()

    start_date, end_date = date_range_for_period(
        period,
        anchor_date.isoformat(),
        custom_start=custom_start,
        custom_end=custom_end,
    )
    selected_records = filter_records_by_date(records, start_date, end_date)
    metrics = summary_metrics(selected_records)
    target_metrics = target_metrics_for_period(
        start_date,
        end_date,
        anchor_date=anchor_date.isoformat(),
    )
    st.caption(f"统计范围：{start_date} 至 {end_date}")
    st.caption(
        "目标口径：每日摘录 10 条、被采纳 2 条、采纳率 20%。"
        f"当前按 {target_metrics['start_date']} 至 {target_metrics['end_date']} "
        f"的 {target_metrics['reporting_days']} 个上报日计算，"
        f"目标摘录 {target_metrics['total']} 条、目标采纳 {target_metrics['accepted']} 条。"
    )

    metric_cols = st.columns(3)
    metric_cols[0].metric(
        "总摘录数",
        metrics["total"],
        _format_count_delta(metrics["total"], target_metrics["total"]),
    )
    metric_cols[1].metric(
        "被采纳数",
        metrics["accepted"],
        _format_count_delta(metrics["accepted"], target_metrics["accepted"]),
    )
    metric_cols[2].metric(
        "采纳率",
        f"{metrics['acceptance_rate']}%",
        _format_rate_delta(
            metrics["acceptance_rate"], target_metrics["acceptance_rate"]
        ),
    )

    if not selected_records:
        st.info("当前日期范围内没有摘录记录。")
        return

    detail_frame = analytics_frame(selected_records)
    unclassified_count = (
        int((detail_frame["type"] == "未分类").sum()) if not detail_frame.empty else 0
    )
    inferred_count = (
        int(detail_frame["data_source"].astype(str).str.contains("rules").sum())
        if not detail_frame.empty
        else 0
    )
    if inferred_count:
        st.caption(f"{inferred_count} 条历史记录缺少原始 JSON 元数据，已根据标题和 URL 自动补全分类。")
    if unclassified_count:
        st.caption(f"{unclassified_count} 条历史记录仍缺少足够信息，暂显示为“未分类”。")

    daily_frame = daily_counts_frame(selected_records)
    by_type = distribution_frame(selected_records, "type")
    by_soft = distribution_frame(selected_records, "soft_hard")
    by_source = distribution_frame(selected_records, "source").head(12)
    keyword_frame = keyword_counts(selected_records, limit=18)

    trend_col, insight_col = st.columns([2, 1])
    with trend_col:
        st.markdown("**每日趋势**")
        if not daily_frame.empty:
            st.altair_chart(
                _daily_trend_chart(daily_frame),
                width="stretch",
            )
    with insight_col:
        st.markdown("**本周期重点**")
        _render_period_insights(daily_frame, by_source, by_type)

    col_type, col_soft = st.columns(2)
    with col_type:
        st.markdown("**要闻类型结构**")
        if not by_type.empty:
            st.altair_chart(
                _distribution_share_chart(by_type, "type", "要闻类型"),
                width="stretch",
            )
            st.dataframe(
                _distribution_table(by_type),
                hide_index=True,
                width="stretch",
                column_config={
                    "采纳率": st.column_config.ProgressColumn(
                        "采纳率",
                        min_value=0,
                        max_value=100,
                        format="%.1f%%",
                    )
                },
            )
    with col_soft:
        st.markdown("**软/硬科学结构**")
        if not by_soft.empty:
            st.altair_chart(
                _distribution_share_chart(by_soft, "soft_hard", "软/硬科学"),
                width="stretch",
            )
            st.dataframe(
                _distribution_table(by_soft),
                hide_index=True,
                width="stretch",
                column_config={
                    "采纳率": st.column_config.ProgressColumn(
                        "采纳率",
                        min_value=0,
                        max_value=100,
                        format="%.1f%%",
                    )
                },
            )

    col_source, col_keywords = st.columns([3, 2])
    with col_source:
        st.markdown("**来源排行**")
        if not by_source.empty:
            st.dataframe(
                _distribution_table(by_source),
                hide_index=True,
                width="stretch",
                height=360,
                column_config={
                    "采纳率": st.column_config.ProgressColumn(
                        "采纳率",
                        min_value=0,
                        max_value=100,
                        format="%.1f%%",
                    )
                },
            )
    with col_keywords:
        st.markdown("**高频关键词**")
        if not keyword_frame.empty:
            _render_keyword_chips(keyword_frame)
            st.dataframe(keyword_frame, hide_index=True, width="stretch")

    st.markdown("**明细表**")
    display_frame = detail_frame[
        [
            "date",
            "order_index",
            "accepted",
            "title",
            "type",
            "soft_hard",
            "source",
            "keywords_text",
            "url",
        ]
    ].rename(
        columns={
            "date": "日期",
            "order_index": "序号",
            "accepted": "客户采纳",
            "title": "标题",
            "type": "类型",
            "soft_hard": "软/硬科学",
            "source": "来源",
            "keywords_text": "关键词",
            "url": "URL",
        }
    )
    display_frame = display_frame.sort_values(["日期", "序号"], ascending=[False, True])
    st.dataframe(display_frame, hide_index=True, width="stretch", height=520)

    if st.button("导出当前统计 Excel", type="primary", width="stretch"):
        output_path = export_analytics_excel(
            selected_records, analytics_export_path(start_date, end_date)
        )
        st.success(f"统计 Excel 已生成：{output_path}")


def _format_count_delta(current: int, previous: int) -> str | None:
    delta = int(current) - int(previous)
    if delta == 0:
        return "持平"
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta} 条"


def _format_rate_delta(current: float, previous: float) -> str | None:
    delta = round(float(current) - float(previous), 1)
    if delta == 0:
        return "持平"
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta}%"


def _daily_trend_chart(daily_frame: pd.DataFrame) -> alt.Chart:
    frame = daily_frame.copy()
    frame["采纳率"] = frame.apply(
        lambda row: round(row["被采纳数"] / row["总摘录数"] * 100, 1)
        if row["总摘录数"]
        else 0,
        axis=1,
    )
    base = alt.Chart(frame).encode(
        x=alt.X(
            "date:N",
            title="",
            sort=list(frame["date"]),
            axis=alt.Axis(labelAngle=-35),
        )
    )
    total_bar = base.mark_bar(color="#7cc2ff", opacity=0.7).encode(
        y=alt.Y("总摘录数:Q", title="数量"),
        tooltip=[
            alt.Tooltip("date:N", title="日期"),
            alt.Tooltip("总摘录数:Q", title="总摘录数"),
            alt.Tooltip("被采纳数:Q", title="被采纳数"),
            alt.Tooltip("采纳率:Q", title="采纳率", format=".1f"),
        ],
    )
    accepted_bar = base.mark_bar(color="#47c983", width={"band": 0.28}).encode(
        y=alt.Y("被采纳数:Q", title="数量"),
        tooltip=[
            alt.Tooltip("date:N", title="日期"),
            alt.Tooltip("被采纳数:Q", title="被采纳数"),
        ],
    )
    accepted_line = base.mark_line(
        color="#f2c94c",
        point=alt.OverlayMarkDef(color="#f2c94c", size=70),
        strokeWidth=2,
    ).encode(
        y=alt.Y("被采纳数:Q", title="数量"),
        tooltip=[
            alt.Tooltip("date:N", title="日期"),
            alt.Tooltip("采纳率:Q", title="采纳率", format=".1f"),
        ],
    )
    return alt.layer(total_bar, accepted_bar, accepted_line).properties(height=320)


def _distribution_share_chart(
    frame: pd.DataFrame, column: str, title: str
) -> alt.Chart:
    chart_frame = frame.copy().rename(columns={column: "类别"})
    chart_frame["维度"] = title
    chart_frame["占比"] = chart_frame.apply(
        lambda row: round(row["总摘录数"] / chart_frame["总摘录数"].sum() * 100, 1)
        if chart_frame["总摘录数"].sum()
        else 0,
        axis=1,
    )
    return (
        alt.Chart(chart_frame)
        .mark_bar(size=34)
        .encode(
            x=alt.X("总摘录数:Q", stack="normalize", title="占比"),
            y=alt.Y("维度:N", title=""),
            color=alt.Color(
                "类别:N",
                title="",
                scale=alt.Scale(
                    range=[
                        "#7cc2ff",
                        "#47c983",
                        "#f2c94c",
                        "#ff8a65",
                        "#b48cf2",
                        "#8bd3dd",
                    ]
                ),
            ),
            tooltip=[
                alt.Tooltip("类别:N", title="类别"),
                alt.Tooltip("总摘录数:Q", title="总摘录数"),
                alt.Tooltip("被采纳数:Q", title="被采纳数"),
                alt.Tooltip("采纳率:Q", title="采纳率", format=".1f"),
                alt.Tooltip("占比:Q", title="占比", format=".1f"),
            ],
        )
        .properties(height=96)
    )


def _distribution_table(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    output["采纳率"] = pd.to_numeric(output["采纳率"], errors="coerce").fillna(0.0)
    return output


def _render_period_insights(
    daily_frame: pd.DataFrame,
    by_source: pd.DataFrame,
    by_type: pd.DataFrame,
) -> None:
    best_day_label, best_day_value = _best_daily_insight(daily_frame)
    best_source_label, best_source_value = _best_distribution_insight(by_source, "source")
    main_type_label, main_type_value = _main_distribution_insight(by_type, "type")

    st.metric(best_day_label, best_day_value)
    st.metric(best_source_label, best_source_value)
    st.metric(main_type_label, main_type_value)


def _best_daily_insight(daily_frame: pd.DataFrame) -> tuple[str, str]:
    if daily_frame.empty:
        return "最高采纳日", "暂无"
    ranked = daily_frame.sort_values(["被采纳数", "总摘录数", "date"], ascending=False)
    row = ranked.iloc[0]
    if int(row["被采纳数"]) == 0:
        return "最高采纳日", "暂无采纳"
    return "最高采纳日", f"{row['date']}：{int(row['被采纳数'])}/{int(row['总摘录数'])}"


def _best_distribution_insight(
    frame: pd.DataFrame, column: str
) -> tuple[str, str]:
    if frame.empty:
        return "最高采纳来源", "暂无"
    ranked = frame.sort_values(["被采纳数", "采纳率", "总摘录数"], ascending=False)
    row = ranked.iloc[0]
    if int(row["被采纳数"]) == 0:
        return "最高采纳来源", "暂无采纳"
    return "最高采纳来源", f"{row[column]}：{int(row['被采纳数'])} 条"


def _main_distribution_insight(frame: pd.DataFrame, column: str) -> tuple[str, str]:
    if frame.empty:
        return "主要类型", "暂无"
    row = frame.sort_values(["总摘录数", "被采纳数"], ascending=False).iloc[0]
    return "主要类型", f"{row[column]}：{int(row['总摘录数'])} 条"


def _render_keyword_chips(keyword_frame: pd.DataFrame) -> None:
    chips = []
    for _, row in keyword_frame.iterrows():
        keyword = html.escape(str(row.get("关键词", "")))
        count = int(row.get("出现次数") or 0)
        accepted = int(row.get("被采纳次数") or 0)
        if not keyword:
            continue
        selected_class = " ah-keyword-chip--accepted" if accepted else ""
        accepted_text = f" · 采纳 {accepted}" if accepted else ""
        chips.append(
            f"<span class='ah-keyword-chip{selected_class}'>{keyword}"
            f"<strong>{count}</strong><em>{accepted_text}</em></span>"
        )
    if not chips:
        st.caption("暂无关键词。")
        return
    st.markdown(
        """
        <style>
        .ah-keyword-cloud {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin: 4px 0 14px;
        }
        .ah-keyword-chip {
            display: inline-flex;
            align-items: center;
            gap: 7px;
            border: 1px solid rgba(124, 194, 255, .38);
            background: rgba(124, 194, 255, .10);
            color: rgba(255,255,255,.92);
            padding: 5px 9px;
            border-radius: 999px;
            font-size: 13px;
            line-height: 1;
            white-space: nowrap;
        }
        .ah-keyword-chip--accepted {
            border-color: rgba(71, 201, 131, .50);
            background: rgba(71, 201, 131, .12);
        }
        .ah-keyword-chip strong {
            color: #fff;
            font-size: 12px;
        }
        .ah-keyword-chip em {
            color: rgba(255,255,255,.62);
            font-style: normal;
            font-size: 12px;
        }
        </style>
        <div class="ah-keyword-cloud">
        """
        + "".join(chips)
        + "</div>",
        unsafe_allow_html=True,
    )


def _acceptance_marker_panel() -> None:
    st.subheader("被采纳情况标记")
    settings = _load_user_settings()
    master_path = _master_docx_path_from_settings(settings)
    st.caption(f"总 Word：{master_path}")

    if not master_path.exists():
        st.warning("还没有找到总 Word。请先在左侧“总 Word 设置”里确认路径。")
        if st.button("关闭被采纳情况标记", width="stretch"):
            _go_to_workbench()
            st.rerun()
        return

    entries = load_acceptance_entries(master_path)
    if not entries:
        st.info("总 Word 中还没有识别到每日要闻标题。")
        if st.button("关闭被采纳情况标记", width="stretch"):
            _go_to_workbench()
            st.rerun()
        return

    frame = _acceptance_entries_frame(entries)
    _cache_acceptance_marker_frame(master_path, frame)
    _acceptance_marker_editor_fragment(str(master_path))


@st.fragment
def _acceptance_marker_editor_fragment(master_path_text: str) -> None:
    master_path = Path(master_path_text)
    frame = _cached_acceptance_marker_frame(master_path)
    if frame is None:
        st.warning("被采纳标记表格状态已过期，请关闭后重新打开。")
        return
    editor_key = _acceptance_marker_editor_key(master_path)
    row_keys = _acceptance_marker_row_keys(frame)
    edited = st.data_editor(
        frame,
        hide_index=True,
        width="stretch",
        height=560,
        num_rows="fixed",
        column_order=["accepted", "date", "title", "status", "url"],
        column_config={
            "accepted": st.column_config.CheckboxColumn("客户采纳", default=False),
            "date": st.column_config.TextColumn("日期", width="small"),
            "title": st.column_config.TextColumn("标题", width="large"),
            "status": st.column_config.TextColumn("当前状态", width="small"),
            "url": st.column_config.LinkColumn("URL", width="large"),
        },
        disabled=["date", "title", "status", "url"],
        key=editor_key,
        on_change=_sync_acceptance_marker_state,
        args=(str(master_path), row_keys, editor_key),
    )
    _cache_acceptance_marker_frame(master_path, edited)
    st.caption("已标黄的标题会预先勾选。保存后，未勾选的标题会取消黄色荧光笔。")

    selected_count = int(
        edited[
            (edited["marker_id"].astype(str) != "")
            & (edited["accepted"].astype(bool))
        ].shape[0]
    )
    col_save, col_close = st.columns([1, 1])
    with col_save:
        if st.button("同步高亮到总 Word", type="primary", width="stretch"):
            accepted_marker_ids = [
                str(row.get("marker_id", ""))
                for _, row in edited.iterrows()
                if str(row.get("marker_id", "")) and bool(row.get("accepted", False))
            ]
            result = sync_acceptance_highlights(master_path, accepted_marker_ids)
            st.success(
                f"已同步 {result.item_count} 条标题，其中 {result.accepted_count} 条标记为客户采纳。"
            )
            st.write(f"总 Word：{result.master_path}")
            if result.backup_path:
                st.write(f"备份：{result.backup_path}")
            _clear_acceptance_marker_frame(master_path)
    with col_close:
        if st.button("关闭表单", width="stretch"):
            _clear_acceptance_marker_frame(master_path)
            _go_to_workbench()
            st.rerun()
    st.caption(f"当前勾选：{selected_count} 条。")


def _go_to_workbench() -> None:
    st.session_state["pending_workspace_view"] = WORKSPACE_VIEWS[0]


def _acceptance_entries_frame(entries) -> pd.DataFrame:
    rows = []
    last_date = ""
    for entry in entries:
        if last_date and entry.date != last_date:
            rows.append(
                {
                    "marker_id": "",
                    "accepted": False,
                    "date": "",
                    "title": "",
                    "status": "",
                    "url": "",
                }
            )
        rows.append(
            {
                "marker_id": entry.marker_id,
                "accepted": bool(entry.accepted),
                "date": entry.date,
                "title": f"{entry.order_index}.{entry.title}",
                "status": "已标黄" if entry.accepted else "",
                "url": entry.url,
            }
        )
        last_date = entry.date
    return pd.DataFrame(rows).fillna("")


def _acceptance_marker_editor_key(master_path: Path) -> str:
    return f"acceptance_marker_editor::{master_path}"


def _acceptance_marker_cache_key(master_path: Path) -> str:
    return f"acceptance_marker_frame::{master_path}"


def _cached_acceptance_marker_frame(master_path: Path) -> pd.DataFrame | None:
    cached = st.session_state.get(_acceptance_marker_cache_key(master_path))
    return cached.copy() if isinstance(cached, pd.DataFrame) else None


def _cache_acceptance_marker_frame(master_path: Path, frame: pd.DataFrame) -> None:
    st.session_state[_acceptance_marker_cache_key(master_path)] = frame.copy()


def _clear_acceptance_marker_frame(master_path: Path) -> None:
    st.session_state.pop(_acceptance_marker_cache_key(master_path), None)


def _acceptance_marker_row_keys(frame: pd.DataFrame) -> list[str]:
    if "marker_id" not in frame.columns:
        return []
    return [str(value).strip() for value in frame["marker_id"].tolist()]


def _sync_acceptance_marker_state(
    master_path_text: str,
    visible_marker_ids: list[str],
    editor_key: str,
) -> None:
    master_path = Path(master_path_text)
    frame = _cached_acceptance_marker_frame(master_path)
    if frame is None:
        return
    editor_state = st.session_state.get(editor_key, {})
    edited = _apply_acceptance_marker_changes(frame, visible_marker_ids, editor_state)
    _cache_acceptance_marker_frame(master_path, edited)


def _apply_acceptance_marker_changes(
    original: pd.DataFrame,
    visible_marker_ids: list[str],
    editor_state: Any,
) -> pd.DataFrame:
    output = original.copy()
    if "marker_id" not in output.columns or not isinstance(editor_state, dict):
        return output
    edited_rows = editor_state.get("edited_rows", {})
    if not isinstance(edited_rows, dict):
        return output
    for raw_position, changes in edited_rows.items():
        if not isinstance(changes, dict):
            continue
        try:
            marker_id = visible_marker_ids[int(raw_position)]
        except (TypeError, ValueError, IndexError):
            continue
        if not marker_id:
            continue
        mask = output["marker_id"].astype(str) == marker_id
        for column, value in changes.items():
            if column in output.columns:
                output.loc[mask, column] = value
    return output


def _save_daily_word_from_settings(settings: dict[str, Any]) -> bool:
    return bool(settings.get("save_daily_word", False))


def _default_master_docx_path() -> Path:
    return master_docx_path()


def _load_user_settings() -> dict[str, Any]:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_user_settings(settings: dict[str, Any]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_PATH.exists():
        return values
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _save_env_values(updates: dict[str, str]) -> None:
    existing = _load_env_values()
    existing.update(updates)
    ordered_keys = [
        "LLM_PROVIDER",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "OPENAI_REASONING_EFFORT",
        "OPENAI_USE_RESPONSES",
        "OLLAMA_BASE_URL",
        "OLLAMA_MODEL",
        "OLLAMA_THINK_MODE",
        "OLLAMA_TIMEOUT_SECONDS",
        "REQUEST_TIMEOUT_SECONDS",
        "REQUEST_DELAY_SECONDS",
        "FETCH_LIMIT_PER_SOURCE",
        "LOG_LEVEL",
    ]
    lines = []
    for key in ordered_keys:
        if key in existing:
            lines.append(f"{key}={existing[key]}")
    for key, value in existing.items():
        if key not in ordered_keys:
            lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _valid_email(email: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))


def _show_loading_cat(
    message: str,
    placeholder=None,
    detail: str = "",
    eta: str = "",
    elapsed_seconds: int = 0,
):
    placeholder = placeholder or st.empty()
    placeholder.markdown(
        _loading_cat_html(message, detail, eta, elapsed_seconds),
        unsafe_allow_html=True,
    )
    return placeholder


def _loading_cat_html(
    message: str, detail: str = "", eta: str = "", elapsed_seconds: int = 0
) -> str:
    escaped_message = html.escape(message)
    escaped_detail = html.escape(detail or "准备中")
    escaped_eta = html.escape(eta or "预计时间会随步骤变化")
    elapsed_text = _format_elapsed(elapsed_seconds)
    return f"""
<style>
  .ah-loading-overlay {{
    position: fixed;
    inset: 0;
    z-index: 999999;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
    background:
      repeating-linear-gradient(0deg, rgba(255,255,255,0.92) 0 2px, rgba(246,246,246,0.92) 2px 4px);
    backdrop-filter: blur(2px);
  }}
  .ah-loading-card {{
    box-sizing: border-box;
    width: min(100%, 860px);
    max-height: calc(100vh - 48px);
    overflow: hidden;
    margin: 0;
    padding: 20px 18px 18px;
    color: #111;
    background: #fff;
    border: 3px solid #111;
    box-shadow: 8px 8px 0 #111;
  }}
  .ah-loading-title {{
    margin: 0 0 10px;
    font-weight: 800;
    font-size: 16px;
    line-height: 1.35;
    text-align: center;
  }}
  .ah-loading-content {{
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 24px;
    flex-wrap: wrap;
  }}
  .ah-scene {{
    position: relative;
    width: 220px;
    height: 118px;
    margin: 4px auto 8px;
    image-rendering: pixelated;
  }}
  .ah-pixel {{ position: absolute; box-sizing: border-box; background: #111; border-radius: 0; }}
  .ah-body {{ left: 42px; top: 62px; width: 92px; height: 42px; border: 4px solid #111; background: #fff; }}
  .ah-head {{ left: 72px; top: 28px; width: 62px; height: 52px; border: 4px solid #111; background: #fff; }}
  .ah-ear-left {{ left: 72px; top: 12px; width: 22px; height: 22px; clip-path: polygon(0 100%, 50% 0, 100% 100%); }}
  .ah-ear-right {{ left: 112px; top: 12px; width: 22px; height: 22px; clip-path: polygon(0 100%, 50% 0, 100% 100%); }}
  .ah-eye-left, .ah-eye-right {{ top: 48px; width: 8px; height: 8px; }}
  .ah-eye-left {{ left: 88px; }}
  .ah-eye-right {{ left: 114px; }}
  .ah-nose {{ left: 102px; top: 60px; width: 8px; height: 6px; }}
  .ah-mouth {{
    position: absolute;
    left: 106px;
    top: 68px;
    width: 14px;
    height: 8px;
    border-bottom: 4px solid #111;
    border-right: 4px solid #111;
    background: transparent;
    animation: ah-chomp 0.52s steps(2, end) infinite;
  }}
  .ah-whisker {{ height: 3px; width: 20px; }}
  .ah-w1 {{ left: 66px; top: 60px; }}
  .ah-w2 {{ left: 66px; top: 70px; }}
  .ah-w3 {{ left: 124px; top: 60px; }}
  .ah-w4 {{ left: 124px; top: 70px; }}
  .ah-tail {{
    left: 26px;
    top: 54px;
    width: 26px;
    height: 46px;
    border-left: 6px solid #111;
    border-top: 6px solid #111;
    background: transparent;
    animation: ah-tail 0.8s steps(2, end) infinite;
  }}
  .ah-paw {{
    position: absolute;
    left: 132px;
    top: 82px;
    width: 28px;
    height: 14px;
    border: 4px solid #111;
    background: #fff;
    transform-origin: left center;
    animation: ah-paw 0.52s steps(2, end) infinite;
  }}
  .ah-treat-stick {{
    position: absolute;
    left: 146px;
    top: 64px;
    width: 54px;
    height: 12px;
    border: 3px solid #111;
    background: #fff;
    animation: ah-treat 0.52s steps(2, end) infinite;
  }}
  .ah-treat-tip {{ left: 138px; top: 62px; width: 12px; height: 16px; animation: ah-treat 0.52s steps(2, end) infinite; }}
  .ah-crumb {{ width: 5px; height: 5px; opacity: 0; animation: ah-crumb 1.05s steps(3, end) infinite; }}
  .ah-c1 {{ left: 142px; top: 52px; animation-delay: 0.1s; }}
  .ah-c2 {{ left: 151px; top: 48px; animation-delay: 0.28s; }}
  .ah-c3 {{ left: 158px; top: 56px; animation-delay: 0.44s; }}
  .ah-floor {{ left: 22px; top: 106px; width: 176px; height: 4px; }}
  .ah-elapsed {{ text-align: center; font-size: 13px; font-weight: 800; line-height: 1.4; }}
  .ah-status-panel {{
    width: min(100%, 380px);
    min-height: 120px;
    padding: 14px;
    border: 3px solid #111;
    background: #f7f7f7;
    box-sizing: border-box;
  }}
  .ah-status-label {{ margin: 0 0 6px; font-size: 12px; font-weight: 800; color: #555; }}
  .ah-status-text {{ margin: 0 0 14px; font-size: 16px; font-weight: 800; line-height: 1.45; }}
  .ah-eta {{ margin: 0; font-size: 13px; line-height: 1.45; }}
  .ah-hint {{ margin-top: 6px; text-align: center; font-size: 12px; color: #333; }}
  @keyframes ah-chomp {{ 0%, 100% {{ transform: translateY(0); height: 8px; }} 50% {{ transform: translateY(-3px); height: 4px; }} }}
  @keyframes ah-paw {{ 0%, 100% {{ transform: rotate(0deg) translateX(0); }} 50% {{ transform: rotate(-6deg) translateX(4px); }} }}
  @keyframes ah-treat {{ 0%, 100% {{ transform: translateX(0); }} 50% {{ transform: translateX(-6px); }} }}
  @keyframes ah-tail {{ 0%, 100% {{ transform: translateY(0); }} 50% {{ transform: translateY(-4px); }} }}
  @keyframes ah-crumb {{
    0% {{ opacity: 0; transform: translate(0, 0); }}
    33% {{ opacity: 1; transform: translate(3px, -3px); }}
    66% {{ opacity: 1; transform: translate(6px, 2px); }}
    100% {{ opacity: 0; transform: translate(9px, 4px); }}
  }}
</style>
<div class="ah-loading-overlay">
  <div class="ah-loading-card">
    <p class="ah-loading-title">{escaped_message}</p>
    <div class="ah-loading-content">
      <div>
        <div class="ah-scene" aria-label="pixel cat eating a cat treat">
          <div class="ah-pixel ah-tail"></div>
          <div class="ah-pixel ah-body"></div>
          <div class="ah-pixel ah-ear-left"></div>
          <div class="ah-pixel ah-ear-right"></div>
          <div class="ah-pixel ah-head"></div>
          <div class="ah-pixel ah-eye-left"></div>
          <div class="ah-pixel ah-eye-right"></div>
          <div class="ah-pixel ah-nose"></div>
          <div class="ah-mouth"></div>
          <div class="ah-pixel ah-whisker ah-w1"></div>
          <div class="ah-pixel ah-whisker ah-w2"></div>
          <div class="ah-pixel ah-whisker ah-w3"></div>
          <div class="ah-pixel ah-whisker ah-w4"></div>
          <div class="ah-paw"></div>
          <div class="ah-treat-stick"></div>
          <div class="ah-pixel ah-treat-tip"></div>
          <div class="ah-pixel ah-crumb ah-c1"></div>
          <div class="ah-pixel ah-crumb ah-c2"></div>
          <div class="ah-pixel ah-crumb ah-c3"></div>
          <div class="ah-pixel ah-floor"></div>
        </div>
        <div class="ah-elapsed">已用时间：{elapsed_text}</div>
      </div>
      <div class="ah-status-panel">
        <p class="ah-status-label">当前正在操作</p>
        <p class="ah-status-text">{escaped_detail}</p>
        <p class="ah-status-label">预计时间</p>
        <p class="ah-eta">{escaped_eta}</p>
      </div>
    </div>
    <div class="ah-hint">正在处理，请保持这个窗口打开</div>
  </div>
</div>
"""


def _format_elapsed(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class _JobCancelled(Exception):
    pass


def _render_active_job(loading_area) -> bool:
    job = st.session_state.get("active_job")
    if not job:
        return False

    _drain_job_events(job)
    if _job_is_running(job):
        with loading_area.container():
            _active_job_fragment()
        return True

    loading_area.empty()
    if job.get("status") == "running":
        job["status"] = "error"
        job["error"] = "任务已结束，但没有返回完成结果。请检查日志后重试。"
    if job.get("status") == "done":
        st.success(str(job.get("result", "任务已完成。")))
        for line in job.get("details", []):
            st.write(line)
        st.session_state.pop("active_job", None)
        return False
    if job.get("status") == "cancelled":
        st.warning("已停止当前工作。已完成的中间文件会保留，未完成步骤不会继续。")
        st.session_state.pop("active_job", None)
        return False
    if job.get("status") == "error":
        st.error(str(job.get("error", "任务失败。")))
        st.session_state.pop("active_job", None)
        return False
    return False


@st.fragment(run_every="1s")
def _active_job_fragment() -> None:
    job = st.session_state.get("active_job")
    if not job:
        return
    _drain_job_events(job)
    if not _job_is_running(job):
        st.rerun(scope="app")
    _show_job_progress_panel(job)
    _stop_job_controls(job)


def _job_is_running(job: dict[str, Any]) -> bool:
    thread = job.get("thread")
    return bool(thread and thread.is_alive() and job.get("status") == "running")


def _show_job_progress_panel(job: dict[str, Any]) -> None:
    elapsed = int(time.time() - float(job.get("started_at", time.time())))
    current = job.get("current")
    total = job.get("total")
    stop_requested = bool(job.get("stop_requested"))
    progress_text = "当前阶段处理中"
    progress_value = 0.0
    if total:
        progress_value = min(1.0, max(0.0, float(current or 0) / float(total)))
        progress_text = f"{int(current or 0)}/{int(total)}"

    with st.container(border=True):
        title_col, hint_col = st.columns([2, 3])
        title_col.markdown(f"**{job.get('title', '正在处理')}**")
        hint_col.caption("只刷新当前状态区，候选表和其它内容保持稳定。")
        if total:
            st.progress(progress_value, text=f"进度：{progress_text}")
        cat_col, status_col = st.columns([1, 5])
        with cat_col:
            st.markdown(_job_cat_html(stop_requested), unsafe_allow_html=True)
        with status_col:
            cols = st.columns([1, 1, 3])
            cols[0].metric("已用时间", _format_elapsed(elapsed))
            cols[1].metric("进度", "正在停止" if stop_requested else progress_text)
            cols[2].write(f"**当前步骤**  \n{job.get('message', '准备中')}")
            eta = str(job.get("eta", "")).strip()
            if eta:
                st.caption(f"预计：{eta}")


def _job_cat_html(stop_requested: bool = False) -> str:
    mood_class = " ah-job-cat-stopping" if stop_requested else ""
    status_label = "等待停下" if stop_requested else "处理资料"
    return f"""
<style>
  .ah-job-cat-wrap {{
    width: 145px;
    min-height: 112px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 6px;
    overflow: hidden;
  }}
  .ah-job-cat {{
    position: relative;
    width: 170px;
    height: 118px;
    image-rendering: pixelated;
    transform: scale(.72);
    transform-origin: center;
    margin: -13px 0 -17px;
  }}
  .ah-job-cat div {{
    position: absolute;
    box-sizing: border-box;
    border-radius: 0;
  }}
  .ah-jc-body {{
    left: 42px;
    top: 54px;
    width: 78px;
    height: 42px;
    background: #8f8a82;
    border: 4px solid #2b2a28;
  }}
  .ah-jc-chest {{
    left: 70px;
    top: 66px;
    width: 36px;
    height: 32px;
    background: #f3efe5;
    border-left: 4px solid #2b2a28;
    border-top: 4px solid #2b2a28;
  }}
  .ah-jc-head {{
    left: 56px;
    top: 24px;
    width: 58px;
    height: 50px;
    background: #8f8a82;
    border: 4px solid #2b2a28;
    animation: ah-job-cat-chomp 1s steps(2, end) infinite;
  }}
  .ah-jc-face {{
    left: 82px;
    top: 36px;
    width: 28px;
    height: 34px;
    background: #f3efe5;
    border-left: 4px solid #2b2a28;
  }}
  .ah-jc-ear-left {{
    left: 54px;
    top: 8px;
    width: 22px;
    height: 22px;
    background: #8f8a82;
    border-left: 4px solid #2b2a28;
    border-top: 4px solid #2b2a28;
    transform: rotate(45deg);
  }}
  .ah-jc-ear-right {{
    left: 92px;
    top: 8px;
    width: 22px;
    height: 22px;
    background: #8f8a82;
    border-right: 4px solid #2b2a28;
    border-top: 4px solid #2b2a28;
    transform: rotate(45deg);
  }}
  .ah-jc-eye-left,
  .ah-jc-eye-right {{
    top: 43px;
    width: 8px;
    height: 10px;
    background: #cde86a;
    border: 2px solid #2b2a28;
  }}
  .ah-jc-eye-left {{ left: 70px; }}
  .ah-jc-eye-right {{ left: 94px; }}
  .ah-jc-nose {{
    left: 88px;
    top: 56px;
    width: 8px;
    height: 6px;
    background: #d28b86;
    border: 2px solid #2b2a28;
  }}
  .ah-jc-mouth {{
    left: 98px;
    top: 61px;
    width: 14px;
    height: 9px;
    border-right: 4px solid #2b2a28;
    border-bottom: 4px solid #2b2a28;
    background: transparent;
    animation: ah-job-cat-mouth 1s steps(2, end) infinite;
  }}
  .ah-jc-whisker {{
    height: 3px;
    width: 20px;
    background: #2b2a28;
  }}
  .ah-jc-w1 {{ left: 48px; top: 54px; }}
  .ah-jc-w2 {{ left: 48px; top: 63px; }}
  .ah-jc-w3 {{ left: 108px; top: 54px; }}
  .ah-jc-w4 {{ left: 108px; top: 63px; }}
  .ah-jc-tail-a {{
    left: 16px;
    top: 40px;
    width: 30px;
    height: 50px;
    background: #b8b0a6;
    border: 4px solid #2b2a28;
    animation: ah-job-cat-tail 1s steps(2, end) infinite;
  }}
  .ah-jc-tail-b {{
    left: 8px;
    top: 22px;
    width: 26px;
    height: 34px;
    background: #b8b0a6;
    border: 4px solid #2b2a28;
    animation: ah-job-cat-tail-tip 1s steps(2, end) infinite;
  }}
  .ah-jc-paw {{
    left: 112px;
    top: 74px;
    width: 24px;
    height: 14px;
    background: #f3efe5;
    border: 4px solid #2b2a28;
    animation: ah-job-cat-paw 1s steps(2, end) infinite;
  }}
  .ah-jc-treat {{
    left: 124px;
    top: 58px;
    width: 48px;
    height: 13px;
    background: #ffd47a;
    border: 3px solid #2b2a28;
    animation: ah-job-cat-treat 1s steps(2, end) infinite;
  }}
  .ah-jc-treat-tip {{
    left: 116px;
    top: 57px;
    width: 12px;
    height: 15px;
    background: #f29c51;
    border: 3px solid #2b2a28;
    animation: ah-job-cat-treat 1s steps(2, end) infinite;
  }}
  .ah-jc-crumb {{
    width: 5px;
    height: 5px;
    background: #ffd47a;
    opacity: 0;
    animation: ah-job-cat-crumb 1s steps(3, end) infinite;
  }}
  .ah-jc-c1 {{ left: 121px; top: 49px; animation-delay: 0.12s; }}
  .ah-jc-c2 {{ left: 134px; top: 44px; animation-delay: 0.28s; }}
  .ah-jc-c3 {{ left: 143px; top: 52px; animation-delay: 0.44s; }}
  .ah-jc-floor {{
    left: 12px;
    top: 100px;
    width: 148px;
    height: 4px;
    background: #3a3835;
  }}
  .ah-job-cat-label {{
    width: 150px;
    text-align: center;
    font-size: 12px;
    font-weight: 700;
    color: #8f96a3;
  }}
  .ah-job-cat-stopping .ah-jc-treat,
  .ah-job-cat-stopping .ah-jc-treat-tip,
  .ah-job-cat-stopping .ah-jc-paw,
  .ah-job-cat-stopping .ah-jc-mouth,
  .ah-job-cat-stopping .ah-jc-head {{
    animation-duration: 1.6s;
  }}
  @keyframes ah-job-cat-chomp {{
    0%, 100% {{ transform: translateY(0); }}
    50% {{ transform: translateY(2px); }}
  }}
  @keyframes ah-job-cat-mouth {{
    0%, 100% {{ transform: translateY(0); height: 9px; }}
    50% {{ transform: translateY(-2px); height: 5px; }}
  }}
  @keyframes ah-job-cat-paw {{
    0%, 100% {{ transform: translateX(0); }}
    50% {{ transform: translateX(5px); }}
  }}
  @keyframes ah-job-cat-treat {{
    0%, 100% {{ transform: translateX(0); }}
    50% {{ transform: translateX(-7px); }}
  }}
  @keyframes ah-job-cat-tail {{
    0%, 100% {{ transform: translateY(0); }}
    50% {{ transform: translateY(-5px); }}
  }}
  @keyframes ah-job-cat-tail-tip {{
    0%, 100% {{ transform: translateY(0) rotate(-8deg); }}
    50% {{ transform: translateY(-5px) rotate(4deg); }}
  }}
  @keyframes ah-job-cat-crumb {{
    0% {{ opacity: 0; transform: translate(0, 0); }}
    33% {{ opacity: 1; transform: translate(2px, -4px); }}
    66% {{ opacity: 1; transform: translate(5px, 2px); }}
    100% {{ opacity: 0; transform: translate(8px, 4px); }}
  }}
</style>
<div class="ah-job-cat-wrap">
  <div class="ah-job-cat{mood_class}" aria-label="灰白长毛小猫吃猫条的像素动画">
    <div class="ah-jc-tail-a"></div>
    <div class="ah-jc-tail-b"></div>
    <div class="ah-jc-body"></div>
    <div class="ah-jc-chest"></div>
    <div class="ah-jc-ear-left"></div>
    <div class="ah-jc-ear-right"></div>
    <div class="ah-jc-head"></div>
    <div class="ah-jc-face"></div>
    <div class="ah-jc-eye-left"></div>
    <div class="ah-jc-eye-right"></div>
    <div class="ah-jc-nose"></div>
    <div class="ah-jc-mouth"></div>
    <div class="ah-jc-whisker ah-jc-w1"></div>
    <div class="ah-jc-whisker ah-jc-w2"></div>
    <div class="ah-jc-whisker ah-jc-w3"></div>
    <div class="ah-jc-whisker ah-jc-w4"></div>
    <div class="ah-jc-paw"></div>
    <div class="ah-jc-treat"></div>
    <div class="ah-jc-treat-tip"></div>
    <div class="ah-jc-crumb ah-jc-c1"></div>
    <div class="ah-jc-crumb ah-jc-c2"></div>
    <div class="ah-jc-crumb ah-jc-c3"></div>
    <div class="ah-jc-floor"></div>
  </div>
  <div class="ah-job-cat-label">{status_label}</div>
</div>
"""


def _stop_job_controls(job: dict[str, Any]) -> None:
    message_col, button_col = st.columns([5, 1])
    if job.get("stop_requested"):
        message_col.warning("正在停止；当前网页访问或单次模型调用结束后会退出。")
        button_col.button("正在停止", width="stretch", disabled=True)
        return
    message_col.caption("任务可在安全步骤结束后停止，已完成的中间文件会保留。")
    if button_col.button("停止", width="stretch"):
        _request_job_stop(job)
        st.rerun()


def _request_job_stop(job: dict[str, Any]) -> None:
    stop_event = job.get("stop_event")
    if stop_event is not None:
        stop_event.set()
    job["stop_requested"] = True
    job["stop_confirm"] = False
    job["message"] = "正在停止，请等待当前安全步骤结束"
    job["eta"] = "通常会在当前网页访问或单次模型调用结束后停止。"


def _drain_job_events(job: dict[str, Any]) -> None:
    events = job.get("queue")
    if events is None:
        return
    while True:
        try:
            event = events.get_nowait()
        except queue.Empty:
            break
        kind = event.get("kind")
        if kind == "progress":
            if not job.get("stop_requested"):
                job["message"] = event.get("message", job.get("message", ""))
                job["eta"] = event.get("eta", job.get("eta", ""))
            job["current"] = event.get("current")
            job["total"] = event.get("total")
        elif kind == "done":
            if job.get("stop_requested"):
                job["status"] = "cancelled"
            else:
                job["status"] = "done"
                job["result"] = event.get("result", "任务已完成。")
                job["details"] = event.get("details", [])
        elif kind == "cancelled":
            job["status"] = "cancelled"
        elif kind == "error":
            if job.get("stop_requested"):
                job["status"] = "cancelled"
            else:
                job["status"] = "error"
                job["error"] = event.get("error", "任务失败。")


def _start_collect_job(run_date: str, compact: str) -> None:
    if _has_running_job():
        st.warning("已有任务正在运行，请先等待或停止当前任务。")
        return
    events: queue.Queue = queue.Queue()
    stop_event = threading.Event()
    thread = threading.Thread(
        target=_collect_job_worker,
        args=(run_date, compact, events, stop_event),
        daemon=True,
    )
    st.session_state["active_job"] = {
        "thread": thread,
        "queue": events,
        "stop_event": stop_event,
        "started_at": time.time(),
        "status": "running",
        "title": "正在生成今日候选池",
        "message": "准备读取网站配置",
        "eta": "一般 3-8 分钟；如果包含日经或周一扩容，可能更久。",
        "stop_confirm": False,
        "stop_requested": False,
    }
    thread.start()


def _start_word_job(
    run_date: str,
    reviewed_path: Path,
    master_docx_path: Path,
    save_daily_word: bool,
) -> None:
    if _has_running_job():
        st.warning("已有任务正在运行，请先等待或停止当前任务。")
        return
    events: queue.Queue = queue.Queue()
    stop_event = threading.Event()
    thread = threading.Thread(
        target=_word_job_worker,
        args=(
            run_date,
            reviewed_path,
            master_docx_path,
            save_daily_word,
            events,
            stop_event,
        ),
        daemon=True,
    )
    st.session_state["active_job"] = {
        "thread": thread,
        "queue": events,
        "stop_event": stop_event,
        "started_at": time.time(),
        "status": "running",
        "title": "正在生成并追加总 Word",
        "message": "准备读取已勾选新闻",
        "eta": "通常每条 20-60 秒；本地深度思考模式会更慢。",
        "stop_confirm": False,
        "stop_requested": False,
    }
    thread.start()


def _has_running_job() -> bool:
    job = st.session_state.get("active_job")
    thread = job.get("thread") if job else None
    return bool(thread and thread.is_alive() and job.get("status") == "running")


def _emit_job_progress(
    events: queue.Queue,
    message: str,
    eta: str = "",
    current: int | None = None,
    total: int | None = None,
) -> None:
    events.put(
        {
            "kind": "progress",
            "message": message,
            "eta": eta,
            "current": current,
            "total": total,
        }
    )


def _raise_if_stopped(stop_event: threading.Event) -> None:
    if stop_event.is_set():
        raise _JobCancelled()


def _collect_job_worker(
    run_date: str, compact: str, events: queue.Queue, stop_event: threading.Event
) -> None:
    try:
        public_summary = ""
        nikkei_summary = ""
        _emit_job_progress(events, "读取候选池网站配置", "马上开始抓取。")
        sources = load_sources()
        enabled_sources = [source for source in sources if source.enabled]
        if not enabled_sources:
            events.put({"kind": "error", "error": "候选池里没有启用任何网站。"})
            return
        has_public_sources = any(
            source.source_type not in {"login_browser", "manual"}
            for source in enabled_sources
        )
        nikkei_source = next(
            (source for source in enabled_sources if source.name == "Nikkei"), None
        )
        clear_run_date(run_date)
        _raise_if_stopped(stop_event)

        if has_public_sources:
            _emit_job_progress(events, "公开网站：发现候选链接", "约 30 秒到 2 分钟。")
            discovered = discover_sources(run_date, stop_event=stop_event)
            _raise_if_stopped(stop_event)

            def collect_progress(index: int, total: int, item: dict[str, Any]) -> None:
                source = item.get("source", "公开网站")
                _emit_job_progress(
                    events,
                    f"公开网站：抓取正文 {index}/{total}（{source}）",
                    "按网站限速逐篇抓取，通常 2-6 分钟。",
                    current=index,
                    total=total,
                )

            articles = collect_articles(
                run_date, stop_event=stop_event, progress_callback=collect_progress
            )
            _raise_if_stopped(stop_event)
            _emit_job_progress(events, "公开网站：评分并生成候选表", "约 30 秒到 2 分钟。")
            candidates = build_auto_candidates(run_date, stop_event=stop_event)
            public_summary = (
                f"公开网站：发现 {len(discovered)} 条链接，采集 {len(articles)} 篇文章，候选 {len(candidates)} 条。"
            )
            _raise_if_stopped(stop_event)

        if nikkei_source:
            def nikkei_progress(index: int, total: int, link: dict[str, str]) -> None:
                _emit_job_progress(
                    events,
                    f"日经：抓取会员候选 {index}/{total}",
                    "日经每篇至少间隔 5 秒，通常 5-15 分钟。",
                    current=index,
                    total=total,
                )

            _emit_job_progress(events, "日经：发现并抓取重点栏目", "含 Tech/サイエンス 等栏目。")
            _, _, nikkei_rows = nikkei_collect(
                run_date,
                max_articles=nikkei_source.max_articles_per_run,
                stop_event=stop_event,
                progress_callback=nikkei_progress,
            )
            nikkei_summary = _nikkei_summary(nikkei_rows)
            _raise_if_stopped(stop_event)

        _emit_job_progress(events, "合并候选池并翻译韩文标题", "约 30 秒到 2 分钟。")
        path, count = _write_combined_candidates(run_date, compact, stop_event=stop_event)
        _raise_if_stopped(stop_event)
        events.put(
            {
                "kind": "done",
                "result": f"自动抓取完成，合并候选池 {count} 条。",
                "details": [public_summary, nikkei_summary, f"合并表：{path}"],
            }
        )
    except _JobCancelled:
        events.put({"kind": "cancelled"})
    except Exception as exc:
        events.put({"kind": "error", "error": f"生成候选池失败：{exc}"})


def _word_job_worker(
    run_date: str,
    reviewed_path: Path,
    master_docx_path: Path,
    save_daily_word: bool,
    events: queue.Queue,
    stop_event: threading.Event,
) -> None:
    try:
        _emit_job_progress(events, "读取已勾选新闻", "马上开始补抓全文。")
        selected = _selected_rows(reviewed_path)
        if not selected:
            events.put({"kind": "error", "error": "还没有勾选采纳新闻。"})
            return
        _raise_if_stopped(stop_event)

        def fulltext_progress(index: int, total: int, item: dict[str, Any]) -> None:
            _emit_job_progress(
                events,
                f"补抓全文 {index}/{total}",
                "日经文章可能较慢；每条约 5-20 秒。",
                current=index,
                total=total,
            )

        selected, fulltext_report = enrich_selected_candidates(
            selected, stop_event=stop_event, progress_callback=fulltext_progress
        )
        _raise_if_stopped(stop_event)
        limited = [item for item in fulltext_report if item.get("status") == "short"]
        skipped = [item for item in fulltext_report if item.get("status") == "failed"]
        if not selected:
            events.put(
                {
                    "kind": "error",
                    "error": "没有可生成摘要的全文新闻。请检查日经登录状态，或改用手动导入正文。",
                }
            )
            return

        def digest_progress(index: int, total: int, item: dict[str, Any]) -> None:
            _emit_job_progress(
                events,
                f"生成中文摘要 {index}/{total}",
                "本地模型通常每条 20-60 秒；深度思考模式更慢。",
                current=index,
                total=total,
            )

        quality_skipped: list[dict[str, Any]] = []
        digests = generate_digests(
            selected,
            run_date,
            stop_event=stop_event,
            progress_callback=digest_progress,
            quality_report=quality_skipped,
        )
        _raise_if_stopped(stop_event)
        if not digests:
            events.put(
                {
                    "kind": "error",
                    "error": "所有已选新闻的摘要均未通过质量检查，未更新总 Word。请稍后重试或切换模型。",
                }
            )
            return
        replace_digests(run_date, digests)
        json_path = save_final_json(digests, run_date)
        _emit_job_progress(events, "更新累计 JSON", "保留统计和可视化数据源。")
        cumulative_json_path, blocks = merge_final_json_into_cumulative(run_date)
        _raise_if_stopped(stop_event)

        daily_docx_path = None
        if save_daily_word:
            _emit_job_progress(events, "保存今日单独 Word", "总 Word 仍会继续更新。")
            daily_docx_path = write_digest_docx(digests, run_date)
            _raise_if_stopped(stop_event)

        _emit_job_progress(events, "追加到总 Word", "会先备份总 Word，再替换或追加当天区块。")
        master_result = update_master_digest_docx(digests, run_date, master_docx_path)
        action_label = {
            "appended": "已追加当天区块",
            "replaced": "已替换当天已有区块",
            "appended_duplicate": "已追加重复日期区块",
        }.get(master_result.action, master_result.action)
        details = [
            f"JSON：{json_path}",
            f"累计 JSON：{cumulative_json_path}",
            f"总 Word：{master_result.master_path}",
            f"总 Word 操作：{action_label}，共 {master_result.item_count} 条。",
        ]
        if master_result.backup_path:
            details.append(f"备份：{master_result.backup_path}")
        if daily_docx_path:
            details.append(f"今日单独 Word：{daily_docx_path}")
        if skipped:
            details.insert(0, f"{len(skipped)} 条已选新闻完全缺少可用内容，已跳过。")
        if limited:
            details.insert(0, f"{len(limited)} 条已选新闻正文不足，已使用候选池已有内容生成，请重点复核。")
        if quality_skipped:
            details.insert(
                0,
                f"{len(quality_skipped)} 条摘要未通过质量检查，未写入 Word："
                + "；".join(
                    str(item.get("title_original", ""))[:40]
                    for item in quality_skipped[:5]
                ),
            )
        events.put(
            {
                "kind": "done",
                "result": f"总 Word 已更新；累计日期块 {len(blocks)} 个。",
                "details": details,
            }
        )
    except _JobCancelled:
        events.put({"kind": "cancelled"})
    except Exception as exc:
        events.put({"kind": "error", "error": f"生成并追加总 Word 失败：{exc}"})


def _mask_email(email: str) -> str:
    name, _, domain = email.partition("@")
    if len(name) <= 2:
        masked_name = name[:1] + "*"
    else:
        masked_name = name[:2] + "*" * min(4, len(name) - 2)
    return f"{masked_name}@{domain}"


def _reference_panel() -> None:
    st.subheader("学习资料")
    st.caption("用历史每日要闻学习你们过去采纳的题材、来源和关键词。")
    with st.expander("上传或更新学习资料", expanded=False):
        configured_reference_path = reference_docx_path()
        upload_target = DATA_ROOT / "reference" / "historical_digest.docx"
        if configured_reference_path.exists():
            st.caption(f"当前学习资料：{configured_reference_path}")
        else:
            st.caption(f"当前学习资料不存在：{configured_reference_path}")

        uploaded = st.file_uploader(
            "上传历史要闻 Word",
            type=["docx"],
            help="上传文件只会保存到本机数据目录并重建参考库，不会覆盖外部总 Word。",
        )
        if uploaded is not None and st.button("上传并更新学习资料", width="stretch"):
            upload_target.parent.mkdir(parents=True, exist_ok=True)
            upload_target.write_bytes(uploaded.getbuffer())
            with st.spinner("正在解析上传的历史要闻..."):
                news_path, keywords_path, stats_path = ingest_reference(upload_target)
            st.success("历史 Word 已上传，学习资料已更新。")
            st.write(f"参考库：{news_path}")
            st.write(f"关键词：{keywords_path}")
            st.write(f"统计：{stats_path}")

        if st.button("重新读取已放好的学习资料", width="stretch"):
            with st.spinner("正在解析历史要闻并更新参考库..."):
                news_path, keywords_path, stats_path = ingest_reference()
            st.success("学习资料已更新。")
            st.write(f"参考库：{news_path}")
            st.write(f"关键词：{keywords_path}")
            st.write(f"统计：{stats_path}")


def _candidate_pool_panel() -> None:
    st.subheader("候选池")
    sources_payload = _read_sources_payload()
    sources = sources_payload.get("sources", [])
    names = [str(item.get("name", "")) for item in sources if item.get("name")]

    with st.expander("选择、添加或删除网站", expanded=False):
        settings_frame = _source_settings_frame(sources)
        edited_settings = st.data_editor(
            settings_frame,
            width="stretch",
            hide_index=True,
            disabled=["name", "country_region", "source_type", "discovery_count"],
            column_config={
                "enabled": st.column_config.CheckboxColumn("启用"),
                "name": st.column_config.TextColumn("网站", width="medium"),
                "country_region": st.column_config.TextColumn("地区"),
                "source_type": st.column_config.TextColumn("类型"),
                "max_articles_per_run": st.column_config.NumberColumn(
                    "候选上限",
                    min_value=1,
                    max_value=200,
                    step=1,
                    help="这个网站每次最多发现/抓取多少条候选新闻。",
                ),
                "discovery_count": st.column_config.NumberColumn("入口数"),
            },
        )
        if st.button("保存网站和上限设置", width="stretch"):
            _update_source_settings(edited_settings)
            st.success("候选池网站和上限已保存。")
            st.rerun()

        selected_names = [
            str(row.get("name", ""))
            for _, row in edited_settings.iterrows()
            if bool(row.get("enabled"))
        ]
        if "Nikkei" in selected_names and st.button("检查会员网站登录", width="stretch"):
            _show_login_status()

        if st.checkbox("显示添加网站"):
            st.caption("建议先到“AI 配置助手”诊断网站；普通公开网站可用规则配置，登录网站需要专用适配器。")
            with st.form("add_source_form", clear_on_submit=True):
                name = st.text_input("网站名称")
                base_url = st.text_input("网站首页或栏目 URL")
                country_region = st.selectbox(
                    "国家/地区", ["Japan", "Korea", "Singapore", "Taiwan", "US", "EU", "Other"]
                )
                language = st.selectbox("语言", ["ja", "ko", "zh", "en", "mixed"])
                source_type = st.selectbox("来源类型", ["html", "official", "rss", "media", "login_browser"])
                priority = st.slider("优先级", min_value=1, max_value=5, value=3)
                max_per_run = st.number_input("每次最多发现文章数", min_value=1, max_value=100, value=12)
                tags = st.text_input("标签，用英文逗号分隔", value="AI,policy,industry")
                discovery_urls = st.text_area("发现入口，每行一个 URL", value=base_url)
                link_selectors = st.text_area(
                    "链接 CSS 选择器（可选，每行一个）",
                    help="例如 article a[href]。留空时使用通用选择器。",
                )
                include_patterns = st.text_area(
                    "只保留的 URL 正则（可选，每行一个）",
                    help="配置后，符合任一规则的链接才会进入候选池。",
                )
                exclude_patterns = st.text_area(
                    "排除的 URL 正则（可选，每行一个）",
                )
                submitted = st.form_submit_button("添加到候选池")
                if submitted:
                    if not name.strip() or not base_url.strip():
                        st.warning("请至少填写网站名称和 URL。")
                    else:
                        _add_source(
                            {
                                "name": name.strip(),
                                "base_url": base_url.strip(),
                                "country_region": country_region,
                                "language": language,
                                "section_url": base_url.strip(),
                                "source_type": source_type,
                                "requires_login": source_type == "login_browser",
                                "priority": int(priority),
                                "enabled": True,
                                "tags": [tag.strip() for tag in tags.split(",") if tag.strip()],
                                "discovery_urls": [
                                    line.strip()
                                    for line in discovery_urls.splitlines()
                                    if line.strip()
                                ]
                                or [base_url.strip()],
                                "link_selectors": _lines(link_selectors),
                                "include_url_patterns": _lines(include_patterns),
                                "exclude_url_patterns": _lines(exclude_patterns),
                                "rate_limit_seconds": 5 if source_type == "login_browser" else 1.5,
                                "max_articles_per_run": int(max_per_run),
                            }
                        )
                        st.success("网站已添加。")
                        st.rerun()

        st.markdown("**删除网站**")
        removable = [name for name in names if name != "Manual Import"]
        if not removable:
            st.caption("当前没有可删除的网站。")
        else:
            delete_name = st.selectbox("选择要删除的网站", removable)
            confirm_delete = st.checkbox("确认删除所选网站")
            if st.button("删除网站", width="stretch"):
                if not confirm_delete:
                    st.warning("请先勾选确认删除。")
                else:
                    _delete_source(delete_name)
                    st.success(f"已删除：{delete_name}")
                    st.rerun()


def _configuration_assistant_panel() -> None:
    st.subheader("AI 配置助手")
    st.caption(
        "与当前选择的模型对话。模型可以推荐科技信息源；XAutoHeadlines 会实际诊断网站后再允许启用。"
    )
    st.info(
        "模型本身没有实时联网验证能力。公开 HTML/RSS 通常可以自动诊断；登录、付费墙、"
        "强 JavaScript 或反爬网站仍需要专用适配器。"
    )
    if "config_assistant_messages" not in st.session_state:
        st.session_state["config_assistant_messages"] = [
            {
                "role": "assistant",
                "content": (
                    "你可以问我一般问题，也可以让我推荐信息源。例如："
                    "“推荐 5 个欧美人工智能与半导体科技新闻来源”。"
                ),
                "recommended_sources": [],
            }
        ]

    clear_col, status_col = st.columns([1, 4])
    with clear_col:
        if st.button("清空对话", width="stretch"):
            st.session_state.pop("config_assistant_messages", None)
            st.session_state.pop("source_diagnostic_reports", None)
            st.rerun()
    with status_col:
        st.caption(_api_status())

    messages = st.session_state["config_assistant_messages"]
    existing_names = [
        str(item.get("name", ""))
        for item in _read_sources_payload().get("sources", [])
        if isinstance(item, dict) and item.get("name")
    ]
    for message_index, message in enumerate(messages):
        role = str(message.get("role") or "assistant")
        with st.chat_message(role):
            st.markdown(str(message.get("content") or ""))
            recommendations = message.get("recommended_sources") or []
            if recommendations:
                _render_source_recommendations(recommendations, message_index)

    prompt = st.chat_input("向当前模型提问，或请它推荐新的科技信息源")
    if prompt:
        messages.append({"role": "user", "content": prompt, "recommended_sources": []})
        try:
            with st.spinner("当前模型正在思考并整理建议..."):
                result = ask_configuration_assistant(messages, existing_sources=existing_names)
            messages.append(
                {
                    "role": "assistant",
                    "content": result["answer"],
                    "recommended_sources": result["recommended_sources"],
                }
            )
        except Exception as exc:
            messages.append(
                {
                    "role": "assistant",
                    "content": f"这次没有成功调用当前模型：{exc}",
                    "recommended_sources": [],
                }
            )
        st.session_state["config_assistant_messages"] = messages
        st.rerun()


def _render_source_recommendations(
    recommendations: list[dict[str, Any]], message_index: int
) -> None:
    st.markdown("**建议的信息源**")
    reports = st.session_state.setdefault("source_diagnostic_reports", {})
    for source_index, source in enumerate(recommendations):
        identity = f"{message_index}_{source_index}_{source.get('base_url', '')}"
        report = reports.get(identity)
        with st.container(border=True):
            st.markdown(f"**{source.get('name', '未命名网站')}**")
            st.write(source.get("reason") or "模型未提供推荐理由。")
            st.caption(
                f"{source.get('country_region', 'Global')} · "
                f"{source.get('source_type', 'html')} · "
                f"{source.get('base_url', '')}"
            )
            st.write("发现入口：", source.get("discovery_urls") or [source.get("base_url")])

            diagnose_col, enable_col, disabled_col = st.columns(3)
            with diagnose_col:
                if st.button("诊断网站", key=f"diagnose_{identity}", width="stretch"):
                    with st.spinner("正在检查入口、候选链接和正文抽取..."):
                        reports[identity] = diagnose_source(
                            source_config_from_suggestion(source)
                        )
                    st.session_state["source_diagnostic_reports"] = reports
                    st.rerun()
            prepared_source = _source_with_diagnostic_patch(source, report)
            with enable_col:
                if st.button(
                    "添加并启用",
                    key=f"enable_{identity}",
                    width="stretch",
                    disabled=not report or report.get("status") != "ready",
                ):
                    _add_source(
                        source_config_from_suggestion(prepared_source, enabled=True)
                    )
                    st.success("网站已添加并启用。")
            with disabled_col:
                if st.button(
                    "保存为禁用配置",
                    key=f"save_disabled_{identity}",
                    width="stretch",
                ):
                    _add_source(
                        source_config_from_suggestion(prepared_source, enabled=False)
                    )
                    st.success("网站已保存，暂未启用。")
            if report:
                _render_source_diagnostic_report(report)


def _source_with_diagnostic_patch(
    source: dict[str, Any], report: dict[str, Any] | None
) -> dict[str, Any]:
    if not report:
        return source
    patch = report.get("suggested_patch")
    return {**source, **patch} if isinstance(patch, dict) else source


def _render_source_diagnostic_report(report: dict[str, Any]) -> None:
    status_labels = {
        "ready": "通过：可以添加并启用",
        "warning": "警告：候选链接可见，但正文抽取不稳定",
        "needs_rules": "需要规则：入口可访问，但未识别文章链接",
        "needs_adapter": "需要专用适配器",
        "unreachable": "无法访问",
    }
    status = str(report.get("status") or "")
    if status == "ready":
        st.success(status_labels[status])
    elif status in {"warning", "needs_rules", "needs_adapter"}:
        st.warning(status_labels.get(status, status))
    else:
        st.error(status_labels.get(status, status))
    st.write(
        f"识别候选链接：{report.get('candidate_count', 0)}；"
        f"正文样本通过："
        f"{sum(bool(item.get('passed')) for item in report.get('sampled_articles', []))}"
        f"/{len(report.get('sampled_articles', []))}"
    )
    for recommendation in report.get("recommendations") or []:
        st.caption(f"• {recommendation}")
    if report.get("suggested_patch"):
        st.caption(f"建议配置：{report['suggested_patch']}")
    if report.get("errors"):
        st.caption("访问错误：" + " | ".join(str(item) for item in report["errors"]))


def _lines(value: str) -> list[str]:
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]


def _read_sources_payload() -> dict[str, Any]:
    if not SOURCES_PATH.exists():
        return {"sources": []}
    try:
        payload = yaml.safe_load(SOURCES_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {"sources": []}
    if not isinstance(payload.get("sources"), list):
        payload["sources"] = []
    return payload


def _write_sources_payload(payload: dict[str, Any]) -> None:
    SOURCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOURCES_PATH.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _source_settings_frame(sources: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for item in sources:
        if not isinstance(item, dict) or item.get("name") == "Manual Import":
            continue
        rows.append(
            {
                "enabled": bool(item.get("enabled")),
                "name": str(item.get("name", "")),
                "country_region": str(item.get("country_region", "")),
                "source_type": str(item.get("source_type", "")),
                "max_articles_per_run": int(item.get("max_articles_per_run", 20) or 20),
                "discovery_count": len(item.get("discovery_urls") or []),
            }
        )
    return pd.DataFrame(rows)


def _update_source_settings(frame: pd.DataFrame) -> None:
    payload = _read_sources_payload()
    settings = {}
    for _, row in frame.iterrows():
        name = str(row.get("name", ""))
        if not name:
            continue
        settings[name] = {
            "enabled": bool(row.get("enabled")),
            "max_articles_per_run": int(row.get("max_articles_per_run") or 20),
        }
    for item in payload.get("sources", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", ""))
        if name in settings:
            item["enabled"] = settings[name]["enabled"]
            item["max_articles_per_run"] = max(
                1, min(200, settings[name]["max_articles_per_run"])
            )
    _write_sources_payload(payload)


def _set_enabled_sources(selected_names: list[str]) -> None:
    payload = _read_sources_payload()
    selected = set(selected_names)
    for item in payload.get("sources", []):
        if isinstance(item, dict):
            item["enabled"] = str(item.get("name", "")) in selected
    _write_sources_payload(payload)


def _add_source(source: dict[str, Any]) -> None:
    payload = _read_sources_payload()
    sources = [item for item in payload.get("sources", []) if isinstance(item, dict)]
    source["discovery_urls"] = source.get("discovery_urls") or [source["base_url"]]
    existing_index = next(
        (index for index, item in enumerate(sources) if item.get("name") == source["name"]),
        None,
    )
    if existing_index is None:
        sources.append(source)
    else:
        sources[existing_index] = {**sources[existing_index], **source}
    payload["sources"] = sources
    _write_sources_payload(payload)


def _delete_source(name: str) -> None:
    payload = _read_sources_payload()
    payload["sources"] = [
        item
        for item in payload.get("sources", [])
        if not isinstance(item, dict) or item.get("name") != name
    ]
    _write_sources_payload(payload)


def _show_login_status() -> None:
    with st.spinner("正在检查日经专用登录状态..."):
        result = test_nikkei_login()
    st.sidebar.write(f"logged_in: {result.get('logged_in')}")
    if result.get("page_title"):
        st.sidebar.write(f"page_title: {result.get('page_title')}")
    if result.get("warning"):
        st.sidebar.warning(str(result.get("warning")))


def _run_all_collect(run_date: str, compact: str, loading_area=None) -> None:
    public_summary = ""
    nikkei_summary = ""
    sources = load_sources()
    enabled_sources = [source for source in sources if source.enabled]
    if not enabled_sources:
        st.error("候选池里没有启用任何网站。请先在“候选池”里选择网站。")
        return

    has_public_sources = any(
        source.source_type not in {"login_browser", "manual"} for source in enabled_sources
    )
    nikkei_source = next((source for source in enabled_sources if source.name == "Nikkei"), None)
    unsupported_login_sources = [
        source.name
        for source in enabled_sources
        if source.source_type == "login_browser" and source.name != "Nikkei"
    ]
    loader = _show_loading_cat("正在生成今日候选池", loading_area)
    try:
        clear_run_date(run_date)

        if has_public_sources:
            discovered = discover_sources(run_date)
            articles = collect_articles(run_date)
            candidates = build_auto_candidates(run_date)
            public_summary = (
                f"公开网站：发现 {len(discovered)} 条链接，采集 {len(articles)} 篇文章，候选 {len(candidates)} 条。"
            )

        if nikkei_source:
            _, _, nikkei_rows = nikkei_collect(
                run_date, max_articles=nikkei_source.max_articles_per_run
            )
            nikkei_summary = _nikkei_summary(nikkei_rows)

        if unsupported_login_sources:
            st.warning(
                "以下会员/复杂网站目前已加入候选池配置，但还没有接入专用抓取器："
                + "、".join(unsupported_login_sources)
            )

        path, count = _write_combined_candidates(run_date, compact)
    finally:
        loader.empty()
    st.success(f"自动抓取完成，合并候选池 {count} 条。")
    st.write(public_summary)
    st.write(nikkei_summary)
    st.write(f"合并表：{path}")
    st.rerun()


def _choose_candidate_file(compact: str) -> Path | None:
    existing = _candidate_files(compact)
    if not existing:
        return None
    if len(existing) == 1:
        return existing[0]
    labels = {path.name: path for path in existing}
    current_name = str(st.session_state.get("candidate_file_choice", ""))
    if current_name not in labels:
        st.session_state["candidate_file_choice"] = existing[0].name
    choice = st.selectbox(
        "候选表版本",
        list(labels.keys()),
        help="通常使用 reviewed（已保存选择）或 combined（最新合并候选池）。",
        key="candidate_file_choice",
    )
    return labels[choice]


def _candidate_files(compact: str) -> list[Path]:
    paths = [
        OUTPUT_DIR / f"candidates_{compact}_reviewed.xlsx",
        OUTPUT_DIR / f"candidates_{compact}_combined.xlsx",
        OUTPUT_DIR / f"nikkei_candidates_{compact}.xlsx",
        OUTPUT_DIR / f"candidates_{compact}.xlsx",
    ]
    return [path for path in paths if path.exists()]


def _preferred_candidate_file(compact: str) -> Path | None:
    existing = _candidate_files(compact)
    return existing[0] if existing else None


def _active_candidate_file(compact: str) -> Path | None:
    existing = _candidate_files(compact)
    if not existing:
        return None
    selected_name = str(st.session_state.get("candidate_file_choice", ""))
    return next(
        (path for path in existing if path.name == selected_name),
        _preferred_candidate_file(compact),
    )


@st.fragment(run_every="1s")
def _selected_count_metric(path: Path | None) -> None:
    frame = _cached_candidate_frame(path)
    st.metric("今日已选择", f"{_selected_count(frame)} 条")


def _cached_candidate_frame(path: Path | None) -> pd.DataFrame | None:
    if path is None:
        return None
    cached_path = str(st.session_state.get("candidate_edited_path", ""))
    cached = st.session_state.get("candidate_edited_frame")
    if cached_path == str(path) and isinstance(cached, pd.DataFrame):
        return _filter_excluded_candidate_rows(cached)
    if path.exists():
        return _load_candidates(path)
    return None


def _cache_candidate_frame(path: Path, frame: pd.DataFrame) -> None:
    st.session_state["candidate_edited_path"] = str(path)
    st.session_state["candidate_edited_frame"] = frame.copy()


@st.fragment
def _candidate_workspace_fragment(candidate_path_text: str, run_date_text: str) -> None:
    candidate_path = Path(candidate_path_text)
    frame = _cached_candidate_frame(candidate_path)
    if frame is None:
        st.warning("候选表状态已过期，请重新选择候选表版本。")
        return
    st.session_state["candidate_raw_text_map"] = {
        str(row.get("candidate_id", "")): str(row.get("raw_text", ""))
        for _, row in frame.iterrows()
        if row.get("raw_text", "")
    }
    edited = _candidate_editor(frame, candidate_path)
    _cache_candidate_frame(candidate_path, edited)

    col_cumulative, col_folder = st.columns([1, 3])
    cumulative_loading_area = st.empty()
    with col_cumulative:
        if st.button("重建累计 Word", width="stretch", disabled=_has_running_job()):
            _export_cumulative(run_date_text, cumulative_loading_area)
    with col_folder:
        st.caption("勾选会自动保留在当前页面状态；需要写入文件时，使用顶部“保存选择”。")

    _preview_selected(edited)


def _selected_count(frame: pd.DataFrame | None) -> int:
    if frame is None or frame.empty or "selected" not in frame.columns:
        return 0
    return int(frame["selected"].apply(is_selected).sum())


def _nikkei_summary(rows: list[dict[str, Any]]) -> str:
    candidate_count = sum(1 for row in rows if str(row.get("title_original", "")).strip())
    warnings = [
        str(row.get("extraction_warning", "")).strip()
        for row in rows
        if str(row.get("extraction_warning", "")).strip()
    ]
    if warnings:
        return f"会员源：候选 {candidate_count} 条。提示：{warnings[0]}"
    return f"会员源：候选 {candidate_count} 条。"


def _write_combined_candidates(
    run_date: str, compact: str, stop_event: threading.Event | None = None
) -> tuple[Path, int]:
    source_paths = [
        OUTPUT_DIR / f"nikkei_candidates_{compact}.xlsx",
        OUTPUT_DIR / f"candidates_{compact}.xlsx",
    ]
    frames = []
    for path in source_paths:
        if path.exists():
            frame = pd.read_excel(path).fillna("")
            frame["candidate_file"] = path.name
            frames.append(frame)
    output_path = OUTPUT_DIR / f"candidates_{compact}_combined.xlsx"
    if not frames:
        return output_path, 0

    if stop_event is not None:
        _raise_if_stopped(stop_event)
    combined = pd.concat(frames, ignore_index=True, sort=False).fillna("")
    combined = _normalize_candidate_frame(combined)
    if stop_event is not None:
        _raise_if_stopped(stop_event)
    reported_urls = load_reported_urls(before_date=run_date)
    combined = _filter_candidate_frame_by_window(combined, run_date, reported_urls)
    if stop_event is not None:
        _raise_if_stopped(stop_event)
    combined = _rescore_candidate_frame(combined)
    combined = _dedupe_candidate_frame(combined)
    if stop_event is not None:
        _raise_if_stopped(stop_event)
    translated_rows = translate_korean_candidate_titles(
        combined.to_dict("records"), stop_event=stop_event
    )
    if stop_event is not None:
        _raise_if_stopped(stop_event)
    combined = pd.DataFrame(translated_rows).fillna("")
    combined["selected"] = False
    combined = _sort_candidates_for_review(combined)
    first_columns = [
        column for column in DISPLAY_COLUMNS + ["raw_text_preview", "candidate_id"]
        if column in combined.columns
    ]
    other_columns = [column for column in combined.columns if column not in first_columns]
    combined[first_columns + other_columns].to_excel(output_path, index=False)
    return output_path, len(combined)


def _filter_candidate_frame_by_window(
    frame: pd.DataFrame, run_date: str, reported_urls: set[str] | None = None
) -> pd.DataFrame:
    reported_urls = reported_urls or set()
    rows = []
    for _, row in frame.iterrows():
        item = row.to_dict()
        url_key = normalize_reported_url(item.get("url", ""))
        if url_key and url_key in reported_urls:
            continue
        if is_feed_url(item.get("url", "")):
            continue
        keep, status = is_in_collection_window(item.get("published_date", ""), run_date)
        if not keep:
            continue
        text = str(item.get("raw_text") or item.get("raw_text_preview") or "").strip()
        if len(text) < MIN_CANDIDATE_TEXT_CHARS:
            continue
        if is_excluded_topic(item):
            continue
        item["extraction_warning"] = append_window_warning(
            item.get("extraction_warning", ""), status
        )
        rows.append(item)
    return pd.DataFrame(rows).fillna("") if rows else frame.iloc[0:0].copy()


def _dedupe_candidate_frame(frame: pd.DataFrame) -> pd.DataFrame:
    rows: dict[str, pd.Series] = {}
    for _, row in frame.iterrows():
        url = str(row.get("url", "")).strip()
        title = str(row.get("title_original", "")).strip()
        key = url or f"title::{title}"
        if not key:
            key = f"row::{len(rows)}"
        old = rows.get(key)
        if old is None or float(row.get("score") or 0) > float(old.get("score") or 0):
            rows[key] = row
    return pd.DataFrame(list(rows.values())).fillna("")


def _sort_candidates_for_review(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    buckets: dict[str, pd.DataFrame] = {}
    working = frame.copy()
    working["_score_sort"] = pd.to_numeric(working.get("score", 0), errors="coerce").fillna(0)
    working["_review_bucket"] = working.apply(
        lambda row: _review_source_bucket(row.to_dict()), axis=1
    )
    for bucket in ["nikkei", "japan_public", "korea", "other"]:
        buckets[bucket] = working[working["_review_bucket"] == bucket].sort_values(
            "_score_sort", ascending=False
        )

    ordered_rows = []
    positions = {bucket: 0 for bucket in buckets}
    pattern = ["nikkei", "nikkei", "japan_public", "korea"]
    while sum(positions[bucket] < len(rows) for bucket, rows in buckets.items()):
        progressed = False
        for bucket in pattern + ["other"]:
            rows = buckets[bucket]
            position = positions[bucket]
            if position >= len(rows):
                continue
            ordered_rows.append(rows.iloc[position])
            positions[bucket] += 1
            progressed = True
        if not progressed:
            break

    if not ordered_rows:
        return frame
    return pd.DataFrame(ordered_rows).drop(
        columns=["_score_sort", "_review_bucket"], errors="ignore"
    )


def _review_source_bucket(item: dict[str, Any]) -> str:
    source = str(item.get("source", "")).lower()
    country = str(item.get("country_region", "")).lower()
    if "nikkei" in source:
        return "nikkei"
    if country == "japan":
        return "japan_public"
    if country == "korea":
        return "korea"
    return "other"


def _rescore_candidate_frame(frame: pd.DataFrame) -> pd.DataFrame:
    keywords = load_keywords()
    references = load_reference_samples()
    rows = []
    for _, row in frame.iterrows():
        item = row.to_dict()
        article = {
            "title_original": item.get("title_original", ""),
            "raw_text": item.get("raw_text", "") or item.get("raw_text_preview", ""),
            "source": item.get("source", ""),
            "country_region": item.get("country_region", ""),
            "language": item.get("language", ""),
            "published_date": item.get("published_date", ""),
            "url": item.get("url", ""),
            "source_domain": item.get("source_domain", ""),
            "source_priority": item.get("source_priority", 3),
            "source_type": item.get("source_type", ""),
            "extraction_warning": item.get("extraction_warning", ""),
        }
        scored = score_candidate(article, keywords, references)
        item.update(
            {
                "candidate_id": item.get("candidate_id") or scored["candidate_id"],
                "score": scored["score"],
                "recommended_reason": scored["recommended_reason"],
                "matched_keywords": scored["matched_keywords"],
                "reference_similarity_score": scored.get("reference_similarity_score", 0),
                "suggested_type": item.get("suggested_type") or scored.get("suggested_type", ""),
                "suggested_soft_hard": item.get("suggested_soft_hard")
                or scored.get("suggested_soft_hard", ""),
                "raw_text_preview": item.get("raw_text_preview")
                or scored.get("raw_text_preview", ""),
            }
        )
        rows.append(item)
    return pd.DataFrame(rows).fillna("")


def _load_candidates(path: Path) -> pd.DataFrame:
    frame = pd.read_excel(path).fillna("")
    frame = _normalize_candidate_frame(frame)
    return _filter_excluded_candidate_rows(frame)


def _filter_excluded_candidate_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    keep = frame.apply(lambda row: not is_excluded_topic(row.to_dict()), axis=1)
    return frame.loc[keep].reset_index(drop=True)


def _normalize_candidate_frame(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    for column in DISPLAY_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    if "candidate_id" not in frame.columns:
        frame["candidate_id"] = frame.apply(
            lambda row: candidate_id_for(row.to_dict()), axis=1
        )
    frame["selected"] = frame["selected"].apply(is_selected)
    return frame


def _candidate_editor(frame: pd.DataFrame, candidate_path: Path) -> pd.DataFrame:
    _show_candidate_source_summary(frame)
    with st.container(key="candidate_filters"):
        search_col, source_col, score_col, selected_col, warning_col = st.columns(
            [2.2, 1.4, 1, 1, 1]
        )
        with search_col:
            search_text = st.text_input(
                "搜索",
                placeholder="搜索原文或中文标题",
                key="candidate_search_text",
            )
        source_options = ["全部"] + sorted(
            source
            for source in frame.get("source", pd.Series(dtype=str)).astype(str).unique()
            if source
        )
        with source_col:
            source_filter = st.selectbox(
                "来源",
                source_options,
                key="candidate_source_filter",
            )
        with score_col:
            min_score = st.number_input(
                "最低分数",
                min_value=0.0,
                max_value=100.0,
                value=0.0,
                step=5.0,
                key="candidate_min_score",
            )
        with selected_col:
            selected_only = st.toggle("仅看已选择", key="candidate_selected_only")
        with warning_col:
            warning_only = st.toggle("仅看质量警告", key="candidate_warning_only")

    filtered = _filter_candidate_editor_frame(
        frame,
        search_text=search_text,
        source_filter=source_filter,
        min_score=float(min_score),
        selected_only=selected_only,
        warning_only=warning_only,
    )
    st.caption(f"当前显示 {len(filtered)} / {len(frame)} 条候选新闻")
    visible = [
        column
        for column in ["candidate_id", *DISPLAY_COLUMNS]
        if column in filtered.columns
    ]
    editor_key = f"candidate_editor::{candidate_path.name}"
    visible_row_keys = _candidate_editor_row_keys(filtered)
    edited_subset = st.data_editor(
        filtered[visible].reset_index(drop=True),
        width="stretch",
        hide_index=True,
        height=560,
        key=editor_key,
        on_change=_sync_candidate_editor_state,
        args=(str(candidate_path), visible_row_keys, editor_key),
        column_config={
            "candidate_id": None,
            "selected": st.column_config.CheckboxColumn("采纳", default=False),
            "score": st.column_config.NumberColumn("分数", format="%.1f"),
            "title_original": st.column_config.TextColumn("标题", width="large"),
            "title_translated_candidate": st.column_config.TextColumn("中文标题", width="large"),
            "recommended_reason": st.column_config.TextColumn("推荐理由", width="large"),
            "matched_keywords": st.column_config.TextColumn("关键词", width="large"),
            "raw_text_preview": st.column_config.TextColumn("正文预览", width="large"),
            "raw_text": None,
            "url": st.column_config.LinkColumn("URL", width="large"),
        },
    )
    edited = _merge_candidate_editor_rows(frame, edited_subset)
    st.caption(f"已选择 {_selected_count(edited)} 条。顶部操作区会显示并使用当前选择。")
    return edited


def _candidate_editor_row_keys(frame: pd.DataFrame) -> list[dict[str, str]]:
    return [
        {
            "candidate_id": str(row.get("candidate_id", "")).strip(),
            "url": str(row.get("url", "")).strip(),
        }
        for _, row in frame.iterrows()
    ]


def _sync_candidate_editor_state(
    path_text: str,
    visible_row_keys: list[dict[str, str]],
    editor_key: str,
) -> None:
    path = Path(path_text)
    frame = _cached_candidate_frame(path)
    if frame is None:
        return
    editor_state = st.session_state.get(editor_key, {})
    edited = _apply_candidate_editor_changes(frame, visible_row_keys, editor_state)
    _cache_candidate_frame(path, edited)


def _apply_candidate_editor_changes(
    original: pd.DataFrame,
    visible_row_keys: list[dict[str, str]],
    editor_state: Any,
) -> pd.DataFrame:
    output = original.copy()
    if not isinstance(editor_state, dict):
        return output
    edited_rows = editor_state.get("edited_rows", {})
    if not isinstance(edited_rows, dict):
        return output
    for raw_position, changes in edited_rows.items():
        if not isinstance(changes, dict):
            continue
        try:
            position = int(raw_position)
            row_key = visible_row_keys[position]
        except (TypeError, ValueError, IndexError):
            continue
        candidate_id = row_key.get("candidate_id", "")
        url = row_key.get("url", "")
        if candidate_id and "candidate_id" in output.columns:
            mask = output["candidate_id"].astype(str) == candidate_id
        elif url and "url" in output.columns:
            mask = output["url"].astype(str) == url
        else:
            continue
        for column, value in changes.items():
            if column in output.columns:
                output.loc[mask, column] = value
    return output


def _filter_candidate_editor_frame(
    frame: pd.DataFrame,
    search_text: str = "",
    source_filter: str = "全部",
    min_score: float = 0.0,
    selected_only: bool = False,
    warning_only: bool = False,
) -> pd.DataFrame:
    filtered = frame.copy()
    if search_text.strip():
        query = search_text.strip()
        title_original = filtered.get("title_original", pd.Series("", index=filtered.index))
        title_cn = filtered.get(
            "title_translated_candidate", pd.Series("", index=filtered.index)
        )
        mask = title_original.astype(str).str.contains(query, case=False, na=False)
        mask |= title_cn.astype(str).str.contains(query, case=False, na=False)
        filtered = filtered[mask]
    if source_filter and source_filter != "全部" and "source" in filtered.columns:
        filtered = filtered[filtered["source"].astype(str) == source_filter]
    if min_score > 0 and "score" in filtered.columns:
        scores = pd.to_numeric(filtered["score"], errors="coerce").fillna(0)
        filtered = filtered[scores >= min_score]
    if selected_only and "selected" in filtered.columns:
        filtered = filtered[filtered["selected"].apply(is_selected)]
    if warning_only and "extraction_warning" in filtered.columns:
        filtered = filtered[filtered["extraction_warning"].astype(str).str.strip() != ""]
    return filtered


def _merge_candidate_editor_rows(
    original: pd.DataFrame, edited_subset: pd.DataFrame
) -> pd.DataFrame:
    if edited_subset.empty:
        return original.copy()
    output = original.copy()
    for _, edited_row in edited_subset.iterrows():
        candidate_id = str(edited_row.get("candidate_id", "")).strip()
        url = str(edited_row.get("url", "")).strip()
        if candidate_id and "candidate_id" in output.columns:
            mask = output["candidate_id"].astype(str) == candidate_id
        elif url and "url" in output.columns:
            mask = output["url"].astype(str) == url
        else:
            continue
        for column in edited_subset.columns:
            if column in output.columns:
                output.loc[mask, column] = edited_row.get(column, "")
    return output


def _show_candidate_source_summary(frame: pd.DataFrame) -> None:
    if frame.empty or "source" not in frame.columns:
        return
    counts = frame["source"].astype(str).replace("", "未知来源").value_counts()
    warnings = (
        int((frame["extraction_warning"].astype(str).str.strip() != "").sum())
        if "extraction_warning" in frame.columns
        else 0
    )
    summary = "；".join(f"{source} {count}条" for source, count in counts.items())
    st.caption(f"共 {len(frame)} 条；质量警告 {warnings} 条；来源分布：{summary}")


def _save_reviewed_candidates(frame: pd.DataFrame, run_date: str) -> Path:
    output = _prepare_reviewed_frame(frame)
    path = OUTPUT_DIR / f"candidates_{compact_date(run_date)}_reviewed.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    output.to_excel(path, index=False)
    return path


def _prepare_reviewed_frame(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    output["selected"] = output["selected"].apply(lambda value: "yes" if value else "")
    for column in ["title_original", "url", "source", "published_date"]:
        if column not in output.columns:
            output[column] = ""
    if "candidate_id" not in output.columns:
        output["candidate_id"] = output.apply(
            lambda row: candidate_id_for(row.to_dict()), axis=1
        )
    raw_text_map = st.session_state.get("candidate_raw_text_map", {})
    if "raw_text" not in output.columns:
        output["raw_text"] = ""
    output["raw_text"] = output.apply(
        lambda row: row.get("raw_text", "")
        or raw_text_map.get(str(row.get("candidate_id", "")), ""),
        axis=1,
    )
    defaults = {
        "country_region": "Japan",
        "language": "ja",
        "source_domain": "www.nikkei.com",
        "source_section": "",
        "title_translated_candidate": "",
        "suggested_type": "技术",
        "suggested_soft_hard": "硬科学",
        "reference_similarity_score": 0,
        "notes": "",
        "duplicate_group": "",
        "raw_text_preview": "",
    }
    for column, value in defaults.items():
        if column not in output.columns:
            output[column] = value
    return output


def _generate_word(run_date: str, reviewed_path: Path, loading_area=None) -> None:
    selected = _selected_rows(reviewed_path)
    if not selected:
        st.error("还没有勾选采纳新闻。")
        return
    provider = os.getenv("LLM_PROVIDER", "openai").strip().lower() or "openai"
    if provider == "openai" and not os.getenv("OPENAI_API_KEY", "").strip():
        st.error("当前未配置 OpenAI API Key，已停止生成，避免把演示摘要写入正式结果。")
        return
    if provider == "ollama":
        status = check_ollama_env(
            os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip(),
            os.getenv("OLLAMA_MODEL", "qwen3:8b").strip() or "qwen3:8b",
        )
        if not status.get("ollama_running") or not status.get("model_installed"):
            st.error("本地 Ollama 模型未就绪，已停止生成，避免把演示摘要写入正式结果。")
            return
    loader = _show_loading_cat("正在补抓全文并追加总 Word", loading_area)
    try:
        selected, fulltext_report = enrich_selected_candidates(selected)
        limited = [item for item in fulltext_report if item.get("status") == "short"]
        skipped = [item for item in fulltext_report if item.get("status") == "failed"]
        if limited:
            st.warning(f"{len(limited)} 条已选新闻正文不足，已使用候选池已有内容生成，请重点复核。")
            for item in limited[:5]:
                st.write(f"- {item.get('title_original')}：{item.get('warning')}")
        if skipped:
            st.warning(f"{len(skipped)} 条已选新闻完全缺少可用内容，已跳过。")
            for item in skipped[:5]:
                st.write(f"- {item.get('title_original')}：{item.get('warning')}")
        if not selected:
            st.error("没有可生成摘要的全文新闻。请检查日经登录状态，或改用手动导入正文。")
            return
        quality_skipped: list[dict[str, Any]] = []
        digests = generate_digests(selected, run_date, quality_report=quality_skipped)
        if quality_skipped:
            st.warning(f"{len(quality_skipped)} 条摘要未通过质量检查，已跳过且不会写入 Word。")
            for item in quality_skipped[:5]:
                st.write(f"- {item.get('title_original')}：{item.get('issue')}")
        if not digests:
            st.error("所有已选新闻的摘要均未通过质量检查，未更新总 Word。请稍后重试或切换模型。")
            return
        replace_digests(run_date, digests)
        json_path = save_final_json(digests, run_date)
        cumulative_json_path, _ = merge_final_json_into_cumulative(run_date)
        settings = _load_user_settings()
        daily_docx_path = (
            write_digest_docx(digests, run_date)
            if _save_daily_word_from_settings(settings)
            else None
        )
        master_result = update_master_digest_docx(
            digests, run_date, _master_docx_path_from_settings(settings)
        )
    finally:
        loader.empty()
    st.success("总 Word 已更新。")
    st.write(f"JSON：{json_path}")
    st.write(f"累计 JSON：{cumulative_json_path}")
    st.write(f"总 Word：{master_result.master_path}")
    if master_result.backup_path:
        st.write(f"备份：{master_result.backup_path}")
    if daily_docx_path:
        st.write(f"今日单独 Word：{daily_docx_path}")


def _selected_rows(path: Path) -> list[dict[str, Any]]:
    frame = pd.read_excel(path).fillna("")
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        item = row.to_dict()
        if is_selected(item.get("selected", "")):
            rows.append(item)
    return rows


def _export_cumulative(run_date: str, loading_area=None) -> None:
    if not final_json_path(run_date).exists():
        st.error("还没有今日摘要 JSON，请先点击“生成并追加总 Word”。")
        return
    loader = _show_loading_cat("正在更新累计 Word", loading_area)
    try:
        json_path, docx_path, blocks = export_cumulative(run_date)
    finally:
        loader.empty()
    count = sum(len(block.get("items", [])) for block in blocks)
    st.success(f"累计 Word 已更新，共 {len(blocks)} 个日期块、{count} 条要闻。")
    st.write(f"累计 JSON：{json_path}")
    st.write(f"累计 Word：{docx_path}")


def _preview_selected(frame: pd.DataFrame) -> None:
    selected = frame[frame["selected"] == True]  # noqa: E712
    st.subheader(f"已选择 {len(selected)} 条")
    for _, row in selected.iterrows():
        with st.expander(str(row.get("title_original", ""))):
            st.write(f"分数：{row.get('score', '')}")
            st.write(f"推荐理由：{row.get('recommended_reason', '')}")
            st.write(f"关键词：{row.get('matched_keywords', '')}")
            if row.get("raw_text_preview", ""):
                st.write(str(row.get("raw_text_preview", ""))[:800])
            if row.get("url", ""):
                st.link_button("打开原文", str(row.get("url")))


def _open_in_finder(path: Path) -> None:
    try:
        if platform.system() == "Darwin":
            subprocess.run(["open", str(path)], check=False)
        elif platform.system() == "Windows":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception as exc:
        st.warning(f"无法自动打开文件夹：{exc}")


if __name__ == "__main__":
    main()
