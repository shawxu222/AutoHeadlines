#!/bin/zsh
PROJECT_DIR="${0:A:h}"

cd "$PROJECT_DIR" || {
  echo "无法打开 AutoHeadlines 项目目录：$PROJECT_DIR"
  read "?按 Enter 退出..."
  exit 1
}

if [ ! -x ".venv/bin/python" ]; then
  echo "没有找到运行环境。请先双击“安装 AutoHeadlines.command”。"
  read "?按 Enter 退出..."
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

read "?AutoHeadlines 已退出，按 Enter 关闭窗口..."
