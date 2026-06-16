#!/bin/zsh
set -euo pipefail

PROJECT_DIR="${0:A:h}"
DEFAULT_MODEL="${XAUTOHEADLINES_OLLAMA_MODEL:-${AUTOHEADLINES_OLLAMA_MODEL:-qwen3:8b}}"
OLLAMA_DOWNLOAD_URL="https://ollama.com/download/mac"
MODEL_LIBRARY_URL="https://ollama.com/library"

cd "$PROJECT_DIR"

find_ollama() {
  if command -v ollama >/dev/null 2>&1; then
    command -v ollama
    return 0
  fi
  for candidate in \
    "/Applications/Ollama.app/Contents/Resources/ollama" \
    "$HOME/Applications/Ollama.app/Contents/Resources/ollama"; do
    if [ -x "$candidate" ]; then
      printf "%s\n" "$candidate"
      return 0
    fi
  done
  return 1
}

start_ollama() {
  if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    return 0
  fi
  open -a Ollama 2>/dev/null || "$OLLAMA_BIN" serve > /tmp/xautoheadlines-ollama.log 2>&1 &
  for _ in {1..30}; do
    curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

printf "\nXAutoHeadlines local model setup (optional)\n"
printf "Ollama: %s\n" "$OLLAMA_DOWNLOAD_URL"
printf "Model library: %s\n" "$MODEL_LIBRARY_URL"
printf "Recommended model: %s (approximately 5.2 GB)\n\n" "$DEFAULT_MODEL"

OLLAMA_BIN="$(find_ollama || true)"
if [ -z "$OLLAMA_BIN" ]; then
  printf "Ollama is not installed. The official download page will open now.\n"
  printf "Install Ollama, then run this optional setup again.\n"
  open "$OLLAMA_DOWNLOAD_URL"
  read "?Press Enter to close..."
  exit 0
fi

if ! start_ollama; then
  printf "Ollama did not start. Open Ollama manually, then run this setup again.\n"
  read "?Press Enter to close..."
  exit 1
fi

read "MODEL?Model to download [$DEFAULT_MODEL]: "
MODEL="${MODEL:-$DEFAULT_MODEL}"
printf "\nDownloading %s through Ollama. This may take a while.\n" "$MODEL"
"$OLLAMA_BIN" pull "$MODEL"

printf "\nThe local model is ready.\n"
printf "Open XAutoHeadlines and select Local Ollama in model settings.\n"
if [ "$MODEL" != "$DEFAULT_MODEL" ]; then
  printf "Set the Ollama model name to: %s\n" "$MODEL"
fi
read "?Press Enter to close..."
