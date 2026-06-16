# XAutoHeadlines

XAutoHeadlines is a local workbench for collecting, reviewing, summarizing, and
analyzing science and technology news.

It is designed around a human-in-the-loop workflow:

```text
discover sources -> collect articles -> rank candidates -> human review
-> AI summaries -> Word/JSON/Excel output -> trend statistics
```

The built-in profile produces a Chinese digest focused on science and technology
news from Japan and South Korea. Profiles can change sources, regions, keywords,
reporting windows, summary prompts, output language, and Word formatting without
rewriting the core pipeline.

## Highlights

- RSS, generic HTML, official-site, manual import, and login-backed source types
- Configurable source priorities, rate limits, tags, and collection windows
- Technology relevance, importance scoring, deduplication, and human review
- Historical accepted-digest learning for source discovery and candidate ranking
- OpenAI API or local Ollama summarization
- In-app chat with the selected model for questions and structured source recommendations
- Source onboarding diagnostics for links, feeds, article extraction, and adapter needs
- DOCX, XLSX, JSON, cumulative digest, and analytics output
- Local browser profile support for sites the user is authorized to access
- Runtime data can stay outside the repository

## Install From Source

Python 3.10 or newer is required.

```bash
git clone https://github.com/YOUR_ACCOUNT/XAutoHeadlines.git
cd XAutoHeadlines
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
xautoheadlines init
xautoheadlines doctor
```

Edit `.env` and configure either OpenAI or Ollama. XAutoHeadlines deliberately
stops summary generation when the configured model is unavailable, so demo text
cannot silently enter a final report.

Start the local interface:

```bash
xautoheadlines review-app
```

The Streamlit server binds to `127.0.0.1` by default. On macOS, the two
`.command` launchers can also be opened directly after installation.

## macOS User Download

GitHub Releases include `XAutoHeadlines-macOS-VERSION.zip` for non-developer
users. After extracting it, open `安装 XAutoHeadlines.command`. The installer
creates the Python environment and installs the browser dependencies.

AI model setup is optional. Users can configure OpenAI API in the app, or install
[Ollama](https://ollama.com/download/mac) and explicitly choose a model from the
[Ollama library](https://ollama.com/library). The package includes
`安装本地模型（可选）.command`; the recommended starting model is
[`qwen3:8b`](https://ollama.com/library/qwen3:8b), approximately 5.2 GB.

Ollama and model weights are not embedded in the Release asset. See
[docs/INSTALL_MACOS.md](docs/INSTALL_MACOS.md) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Profiles

The active profile is selected in `.env`:

```bash
XAUTOHEADLINES_PROFILE=japan-korea-scitech-zh
```

Built-in profiles:

- `japan-korea-scitech-zh`: the original Japan/Korea Chinese digest workflow
- `example-global-scitech-en`: a minimal English example for adaptation

Create a new YAML file under `config/profiles/`, or point
`XAUTOHEADLINES_PROFILE` to an absolute YAML path. See
[docs/PROFILES.md](docs/PROFILES.md).

## Sources And Login Sites

Sources are configured in YAML. Public releases should prefer RSS and official
feeds where possible. Generic HTML discovery is heuristic and some sites need a
dedicated adapter.

Login-backed collection uses a local browser profile and only accesses pages the
user can normally open. XAutoHeadlines does not provide credentials, bypass
paywalls, solve access controls, or grant redistribution rights. The current
release includes a dedicated Nikkei adapter; other login sites are configuration
placeholders until an adapter is implemented.

See [docs/SOURCES.md](docs/SOURCES.md).

The **AI Configuration Assistant** workspace can recommend sources, test their
real entry pages, and add validated public sources. Model recommendations are
not treated as verified facts: XAutoHeadlines checks reachability, candidate-link
discovery, and sample article extraction before enabling a source.

## Runtime Data And Privacy

By default, runtime files are stored under `data/` and ignored by Git. To keep
them outside the clone, set:

```bash
XAUTOHEADLINES_HOME=~/.xautoheadlines
XAUTOHEADLINES_BROWSER_PROFILE_DIR=~/.xautoheadlines/browser-profiles
```

Article text sent to OpenAI leaves the local computer. Ollama keeps model
processing local. Browser cookies, API keys, imported documents, databases,
logs, and generated reports must never be committed.

## Private Daily Workflow

The public repository keeps the complete learning workflow but does not include
any user's historical digest. XAutoHeadlines can use a private master Word file
both as the daily append target and as the learning source.

Set these paths in the local, Git-ignored `data/settings/user_settings.json`:

```json
{
  "master_docx_path": "/private/path/master_digest.docx",
  "reference_docx_path": "/private/path/master_digest.docx",
  "digest_title_template": "Daily Science and Technology Digest {date}:",
  "save_daily_word": false
}
```

Then rebuild the private reference library:

```bash
xautoheadlines ingest-reference
```

The generated reference JSON and statistics are also ignored by Git. Candidate
ranking and selected login-backed source adapters use these reference samples,
while the original Word remains private. See [docs/LEARNING.md](docs/LEARNING.md).

## Common Commands

```bash
xautoheadlines doctor
xautoheadlines daily-auto --date 2026-06-12
xautoheadlines generate --date 2026-06-12 --input data/output/candidates_20260612_reviewed.xlsx
xautoheadlines export-word --date 2026-06-12
xautoheadlines nikkei-login
xautoheadlines check-ollama
```

## Release Scope

Version `0.1.0` is a local, single-user source release. It is not a hosted
multi-user service or a universal web scraper. Some scoring taxonomy and legacy
JSON field names (`title_cn`, `summary_cn`, `soft_hard`) remain for compatibility
with the original workflow and will be migrated carefully in later releases.

## Development

```bash
pip install -e ".[dev]"
pytest -q
ruff check src tests
```

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) and
[SECURITY.md](SECURITY.md) before opening an issue or pull request.

## License

MIT. Website content remains subject to its original copyright, terms of use,
robots policies, and applicable law.
