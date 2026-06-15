from __future__ import annotations

import shutil
import subprocess
from typing import Any

import requests


DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:8b"


def check_ollama_env(
    base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL
) -> dict[str, Any]:
    """Return a small, non-sensitive status report for local Ollama."""
    result: dict[str, Any] = {
        "ollama_command_exists": shutil.which("ollama") is not None,
        "ollama_running": False,
        "model": model,
        "model_installed": False,
        "base_url": base_url,
        "warning": "",
    }
    if result["ollama_command_exists"]:
        try:
            version = subprocess.run(
                ["ollama", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            result["ollama_version"] = (version.stdout or version.stderr).strip()
        except Exception as exc:
            result["ollama_version"] = ""
            result["warning"] = f"无法读取 Ollama 版本：{exc}"

    try:
        response = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=5)
        response.raise_for_status()
        result["ollama_running"] = True
        payload = response.json()
        models = [str(item.get("name", "")) for item in payload.get("models", [])]
        result["installed_models"] = models
        result["model_installed"] = model in models
    except Exception as exc:
        result["installed_models"] = []
        result["warning"] = f"Ollama 服务暂时不可访问：{exc}"

    result["install_ollama_command"] = "brew install ollama"
    result["update_ollama_url"] = "https://ollama.com/download"
    result["start_ollama_command"] = "ollama serve"
    result["pull_model_command"] = f"ollama pull {model}"
    return result
