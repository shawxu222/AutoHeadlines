# Profiles

A Profile describes one science and technology news workflow. It keeps the
collection and review engine stable while allowing different users to change
their focus and output.

```yaml
id: my-scitech-profile
name: My science and technology digest
sources_file: /absolute/path/to/my-sources.yaml
keywords_file: config/keywords.yaml

date_window:
  timezone_label: local time
  start_hour: 8
  normal_lookback_days: 1
  monday_lookback_days: 3

scoring:
  preferred_regions: [Europe]
  require_preferred_region: false
  region_terms: [Europe, European Union, EU]

summary:
  output_language: en
  require_simplified_chinese: false
  max_summary_chars: 1200
  prompt_file: prompts/digest_prompt_en.md

output:
  digest_title_template: "Daily Science and Technology Digest {date}:"
  word_font: Arial
```

`preferred_regions` adds regional relevance to scoring.
`require_preferred_region: true` penalizes articles outside those regions.
Leave both `preferred_regions` and `region_terms` empty for a global workflow.

The `title_cn` and `summary_cn` JSON keys are legacy internal field names in
version 0.1.0. They can contain another output language when a custom profile
sets `require_simplified_chinese: false`.

Never put API keys, cookies, private paths, or copyrighted article text in a
Profile committed to Git.
