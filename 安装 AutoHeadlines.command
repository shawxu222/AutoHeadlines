#!/bin/zsh
set -euo pipefail

PROJECT_DIR="${0:A:h}"
DRY_RUN="${AUTOHEADLINES_INSTALL_DRY_RUN:-0}"

cd "$PROJECT_DIR"

say() {
  printf "\n==> %s\n" "$1"
}

run() {
  if [ "$DRY_RUN" = "1" ]; then
    printf "[dry-run]"
    printf " %q" "$@"
    printf "\n"
    return 0
  fi
  "$@"
}

pause_and_exit() {
  printf "\n%s\n" "$1"
  read "?Press Enter to close..."
  exit 1
}

python_is_supported() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
    >/dev/null 2>&1
}

find_python() {
  local candidate
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && python_is_supported "$candidate"; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

say "Preparing AutoHeadlines"
MEMORY_GB="$(( $(sysctl -n hw.memsize 2>/dev/null || printf '0') / 1024 / 1024 / 1024 ))"
FREE_GB="$(( $(df -Pk "$HOME" | awk 'NR == 2 {print $4}') / 1024 / 1024 ))"
printf "Detected memory: %s GB; free disk space: %s GB.\n" "$MEMORY_GB" "$FREE_GB"
if [ "$FREE_GB" -lt 4 ]; then
  pause_and_exit "At least 4 GB of free disk space is required for installation."
fi

PYTHON_BIN="$(find_python || true)"
if [ -z "$PYTHON_BIN" ]; then
  if command -v brew >/dev/null 2>&1; then
    say "Python 3.10+ was not found; installing Python with Homebrew"
    run brew install python@3.12
    PYTHON_BIN="$(brew --prefix python@3.12)/bin/python3.12"
  else
    if [ "$DRY_RUN" = "1" ]; then
      PYTHON_BIN="python3"
      printf "[dry-run] A real install would require Python 3.10+.\n"
    else
      open "https://www.python.org/downloads/macos/"
      pause_and_exit "Python 3.10+ is required. Install Python, then run this installer again."
    fi
  fi
fi

say "Creating the local Python environment"
run "$PYTHON_BIN" -m venv .venv
run .venv/bin/python -m pip install --upgrade pip
run .venv/bin/python -m pip install -e .
run .venv/bin/python -m playwright install chromium
run .venv/bin/python -m src.main init

say "Checking the installation"
run .venv/bin/python -m src.main doctor

printf "\nAutoHeadlines is ready.\n"
printf "Open '启动 AutoHeadlines.command' to start the application.\n"
printf "\nAI model setup is optional and is not downloaded by this installer.\n"
printf "- OpenAI API: configure it in AutoHeadlines after startup.\n"
printf "- Local Ollama: open '安装本地模型（可选）.command'.\n"
printf "- Ollama download: https://ollama.com/download/mac\n"
printf "- Recommended qwen3:8b model: https://ollama.com/library/qwen3:8b\n"
read "?Press Enter to close..."
