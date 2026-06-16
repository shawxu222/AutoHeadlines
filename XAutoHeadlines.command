#!/bin/zsh
PROJECT_DIR="${0:A:h}"

cd "$PROJECT_DIR" || {
  echo "Cannot open XAutoHeadlines project directory: $PROJECT_DIR"
  read "?Press Enter to exit..."
  exit 1
}

if [ ! -x ".venv/bin/python" ]; then
  echo "Virtual environment not found. Open '安装 XAutoHeadlines.command' first."
  read "?Press Enter to exit..."
  exit 1
fi

if grep -Eq '^LLM_PROVIDER=ollama' .env 2>/dev/null && \
  ! curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  open -a Ollama 2>/dev/null || true
  for _ in {1..15}; do
    curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
    sleep 1
  done
fi

".venv/bin/python" -m src.main review-app

read "?XAutoHeadlines has stopped. Press Enter to close..."
