# Historical Digest Learning

Historical accepted digests are a core XAutoHeadlines input. They improve the
daily workflow without being part of the public repository.

The learning pipeline:

1. Reads a private historical or master Word document.
2. Extracts accepted titles, summaries, URLs, keywords, type, and category hints.
3. Builds local reference JSON and statistics.
4. Uses reference samples during candidate scoring and selected source adapters.
5. Keeps human review as the final decision.

Configure a private source in the Git-ignored file
`data/settings/user_settings.json`:

```json
{
  "master_docx_path": "/private/path/master_digest.docx",
  "reference_docx_path": "/private/path/master_digest.docx",
  "digest_title_template": "Daily Science and Technology Digest {date}:"
}
```

Run `xautoheadlines ingest-reference` after meaningful updates, or use
“重新读取已放好的学习资料” in the interface.

Do not commit the source Word, generated reference JSON, accepted-news
spreadsheets, browser profiles, or final reports.
