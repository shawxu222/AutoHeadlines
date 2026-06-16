# Changelog

## Unreleased

## 0.1.1 - 2026-06-16

- Added an in-app AI configuration assistant using the currently selected model.
- Added structured source recommendations with confirmation before configuration changes.
- Added public-site diagnostics for reachability, feeds, candidate links, and sample extraction.
- Added configurable CSS link selectors and URL include/exclude patterns for source onboarding.
- Added explicit diagnostics for login sites that require dedicated adapters.
- Added a macOS user-download package, first-run installer, and tagged-release workflow.
- Added separate, explicit optional setup for official Ollama and user-selected
  local models; the main installer no longer downloads model weights.
- Renamed the product and macOS user package to XAutoHeadlines.
- Added the Korea MSIT official English source for policy, strategy, AI,
  quantum, ICT, semiconductor, and R&D announcements.
- Fixed candidate-pool and acceptance-marker checkbox interactions so they
  update locally without jumping the workbench back to the top.
- Normalized government `jsessionid` URL fragments during discovery and
  reported-history filtering to prevent duplicate official-source items.
- Added `xautoheadlines` as the primary CLI command while keeping
  `autoheadlines` as a compatibility alias.
- Added `XAUTOHEADLINES_*` environment variables while keeping
  `AUTOHEADLINES_*` compatibility fallbacks for existing local installs.

## 0.1.0 - 2026-06-12

- Prepared AutoHeadlines for its first public source release.
- Added configurable Profiles for sources, keywords, regions, reporting windows,
  summary prompts, languages, and Word output.
- Preserved the original Japan/Korea Chinese workflow as a built-in Profile.
- Added optional external runtime data and browser-profile directories.
- Preserved private master Word and historical-digest learning through
  Git-ignored per-user path settings.
- Removed private organization names and machine-specific launcher paths.
- Stopped silent mock-summary fallback for final output.
- Added packaging metadata, initialization and diagnostics commands, release
  documentation, privacy guidance, and CI.
