## User Download

Download the `AutoHeadlines-macOS-x.y.z.zip` asset, extract it, then open:

`安装 AutoHeadlines.command`

The main installer installs AutoHeadlines and its browser dependencies only. It
does not automatically install an AI provider or download model weights.

Choose either model option after installation:

- **OpenAI API:** start AutoHeadlines and configure an API key in model settings.
- **Local Ollama:** install [Ollama for macOS](https://ollama.com/download/mac),
  then choose a model from the [Ollama model library](https://ollama.com/library).
  The recommended starting model is
  [`qwen3:8b`](https://ollama.com/library/qwen3:8b), approximately 5.2 GB.

The download package also includes `安装本地模型（可选）.command`, which helps
users explicitly download their chosen Ollama model after Ollama is installed.

Ollama and model weights are distributed by their respective projects and are
not bundled in this GitHub Release.
