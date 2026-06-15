from __future__ import annotations

import json
import os
import re
from typing import Any

import requests
from dotenv import load_dotenv

from src.config_loader import PROJECT_ROOT
from src.utils.logger import get_logger


logger = get_logger(__name__)


class OpenAIClient:
    def __init__(self) -> None:
        load_dotenv(PROJECT_ROOT / ".env", override=True)
        self.provider = os.getenv("LLM_PROVIDER", "openai").strip().lower() or "openai"
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.model_label = os.getenv("OPENAI_MODEL", "").strip()
        self.model = _normalize_model_name(self.model_label)
        self.reasoning_effort = os.getenv("OPENAI_REASONING_EFFORT", "high").strip()
        self.use_responses = os.getenv("OPENAI_USE_RESPONSES", "true").lower() not in {
            "0",
            "false",
            "no",
        }
        self.ollama_base_url = os.getenv(
            "OLLAMA_BASE_URL", "http://127.0.0.1:11434"
        ).strip().rstrip("/")
        self.ollama_model = os.getenv("OLLAMA_MODEL", "qwen3:8b").strip()
        self.ollama_think_mode = (
            os.getenv("OLLAMA_THINK_MODE", "fast").strip().lower() or "fast"
        )
        self.timeout_seconds = float(
            os.getenv("OLLAMA_TIMEOUT_SECONDS")
            or os.getenv("REQUEST_TIMEOUT_SECONDS", "120")
            or 120
        )

    @property
    def is_configured(self) -> bool:
        if self.provider == "ollama":
            return bool(self.ollama_base_url and self.ollama_model)
        return bool(self.api_key and self.model)

    @property
    def generation_mode(self) -> str:
        if not self.is_configured:
            return "unconfigured"
        if self.provider == "ollama":
            return f"ollama:{self.ollama_model}:{self.ollama_think_mode}"
        return f"openai:{self.model_label or self.model}"

    def generate_json(self, system_prompt: str, user_payload: dict[str, Any]) -> dict[str, Any]:
        if not self.is_configured:
            raise RuntimeError("LLM provider is not configured.")

        if self.provider == "ollama":
            return self._generate_json_with_ollama(system_prompt, user_payload)

        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, timeout=self.timeout_seconds)
        if self.use_responses:
            try:
                return self._generate_json_with_responses(client, system_prompt, user_payload)
            except Exception as exc:
                logger.exception("Responses API failed, falling back to Chat Completions: %s", exc)

        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        return _loads_json(content)

    def _generate_json_with_ollama(
        self, system_prompt: str, user_payload: dict[str, Any]
    ) -> dict[str, Any]:
        think_mode = self.ollama_think_mode
        think_instruction = ""
        think_value: bool | None = None
        if think_mode in {"deep", "thinking", "think", "true", "1"}:
            think_instruction = "/think"
            think_value = True
        elif think_mode in {"fast", "quick", "no_think", "no-think", "false", "0"}:
            think_instruction = "/no_think"
            think_value = False

        prompt = (
            f"{system_prompt}\n\n"
            "你必须只输出一个 JSON 对象，不要输出 Markdown，不要输出解释，"
            f"不要输出 <think> 或思考过程。{think_instruction}"
        )
        request_payload: dict[str, Any] = {
            "model": self.ollama_model,
            "messages": [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2},
        }
        if think_value is not None:
            request_payload["think"] = think_value

        response = requests.post(
            f"{self.ollama_base_url}/api/chat",
            json=request_payload,
            timeout=self.timeout_seconds,
        )
        if response.status_code == 400 and "think" in request_payload:
            logger.warning("Ollama rejected think mode parameter; retrying without it.")
            request_payload.pop("think", None)
            response = requests.post(
                f"{self.ollama_base_url}/api/chat",
                json=request_payload,
                timeout=self.timeout_seconds,
            )
        response.raise_for_status()
        payload = response.json()
        content = str(payload.get("message", {}).get("content") or "{}")
        return _loads_json(_strip_thinking(content))

    def _generate_json_with_responses(
        self, client: Any, system_prompt: str, user_payload: dict[str, Any]
    ) -> dict[str, Any]:
        reasoning = None
        if self.reasoning_effort:
            reasoning = {"effort": self.reasoning_effort}
        response = client.responses.create(
            model=self.model,
            reasoning=reasoning,
            input=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
        )
        content = getattr(response, "output_text", "") or ""
        return _loads_json(content)


def _normalize_model_name(model: str) -> str:
    normalized = model.strip()
    alias = normalized.lower().replace("_", "-").replace(" ", "")
    if alias in {"gpt5.5thinking", "gpt-5.5-thinking", "gpt-5.5thinking"}:
        return "gpt-5.5"
    return normalized


def _loads_json(content: str) -> dict[str, Any]:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def _strip_thinking(content: str) -> str:
    return re.sub(r"<think>.*?</think>", "", content, flags=re.S | re.I).strip()
