# Install XAutoHeadlines On macOS

## User Download

Download `XAutoHeadlines-macOS-VERSION.zip` from the GitHub Release page, extract
it, and open:

```text
安装 XAutoHeadlines.command
```

The installer:

1. Finds Python 3.10 or newer, or installs Python with Homebrew when available.
2. Creates a private `.venv` inside the XAutoHeadlines folder.
3. Installs XAutoHeadlines and the Playwright Chromium browser.
4. Runs `xautoheadlines init` and `xautoheadlines doctor`.

The main installer does not download Ollama or model weights. After installation,
users can choose:

- OpenAI API: configure an API key in XAutoHeadlines model settings.
- Local model: install [Ollama for macOS](https://ollama.com/download/mac), then
  choose a model from the [Ollama library](https://ollama.com/library).

The download package includes `安装本地模型（可选）.command` to help explicitly
download a selected Ollama model. The recommended starting model is
[`qwen3:8b`](https://ollama.com/library/qwen3:8b), approximately 5.2 GB and best
suited to machines with at least 16 GB of memory.

After installation, open:

```text
启动 XAutoHeadlines.command
```

## macOS Security Prompt

The first release is not signed with an Apple Developer ID. macOS may block a
downloaded `.command` file. In Finder, Control-click the installer, choose
**Open**, and confirm. Future releases should be code-signed and notarized
before being presented as a native consumer application.

## Why The Model Is A Separate Optional Download

GitHub Release assets must each be under 2 GiB, while `qwen3:8b` is roughly
5.2 GB in Ollama. Letting users choose and download it through Ollama preserves
the official distribution path, lets Ollama manage updates and local storage,
and avoids forcing a local model on OpenAI API users.

The release package contains XAutoHeadlines only. See
[`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).
