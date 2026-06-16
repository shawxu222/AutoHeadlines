# Sources

XAutoHeadlines supports these source types:

- `rss`: preferred for stable public feeds
- `html`, `official`, `media`: heuristic public-page discovery and extraction
- `manual`: XLSX or CSV import
- `login_browser`: requires a dedicated site adapter and a local browser profile

Example RSS source:

```yaml
sources:
  - name: Example Science Feed
    country_region: Global
    language: en
    section_url: https://example.com/science.xml
    source_type: rss
    requires_login: false
    priority: 3
    enabled: true
    tags: [science, research]
    rate_limit_seconds: 2
    max_articles_per_run: 20
```

## AI Configuration Assistant

Open the **AI 配置助手** workspace in the local interface to chat with the
currently selected OpenAI or Ollama model. It can answer ordinary questions and
return structured source recommendations. For each recommendation:

1. Click **诊断网站** to test the real entry page.
2. Review candidate-link and sample article extraction results.
3. Use **添加并启用** only after the diagnostic passes.
4. Use **保存为禁用配置** when a source still needs manual rules or an adapter.

The model does not have live web verification in this feature. Its URLs may be
outdated or incorrect, so recommendations are never enabled without a real
diagnostic.

## Configurable Source Rules

Generic discovery works for many public websites, but every site structures
links differently. Source-specific rules can be stored in YAML instead of
hardcoded into Python:

```yaml
sources:
  - name: Example Research News
    base_url: https://example.org
    section_url: https://example.org/research/news
    discovery_urls:
      - https://example.org/research/news
    source_type: official
    country_region: EU
    language: en
    requires_login: false
    enabled: true
    priority: 4
    link_selectors:
      - article a[href]
      - main .news-list a[href]
    include_url_patterns:
      - ^https?://example\.org/research/news/20\d{2}/
    exclude_url_patterns:
      - /events/
      - /about/
```

- `discovery_urls`: listing pages or feed URLs to inspect
- `discovery_urls` may use `{year}` and `{run_date}` placeholders for changing archive pages
- `link_selectors`: optional CSS selectors for links on listing pages
- `include_url_patterns`: optional regular expressions that explicitly identify article URLs
- `exclude_url_patterns`: optional regular expressions that reject navigation or unrelated URLs

When include or exclude patterns are present, they take priority over the
generic URL heuristics. The diagnostic tool can propose a conservative initial
include pattern, but it cannot guarantee future site-layout compatibility.

## What Automatic Onboarding Can And Cannot Solve

The diagnostic and configurable rules cover most stable public HTML and RSS
sources. They reduce the need to modify code whenever a new source has unusual
sections or URL structures.

A dedicated adapter is still required when a site depends on login sessions,
paywalls, JavaScript-only rendering, CAPTCHA, aggressive bot protection, or
unusual pagination/API behavior. XAutoHeadlines reports these as adapter work
instead of pretending that a generic scraper succeeded.

## Login-backed Sources

Users must log in normally and must have permission to access the content.
Browser profiles remain local and are ignored by Git. An adapter may reuse the
session to collect content, but it must not bypass paywalls, access controls,
CAPTCHAs, or rate limits.

The public release currently has a dedicated adapter only for Nikkei. Other
login sources should remain disabled until a maintained adapter exists.

## Publishing A Source Configuration

Before committing a source:

1. Verify the site's terms of use and robots policy.
2. Prefer metadata, links, and short excerpts over storing or redistributing full text.
3. Use conservative request rates.
4. Do not commit credentials, cookies, member-only text, or captured user data.
5. Add fixture-based tests for any dedicated parser.
