# Contributing

XAutoHeadlines focuses specifically on science and technology news workflows.
Changes should preserve the human review step, avoid paywall bypass behavior,
and keep private runtime data outside Git.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
ruff check src tests
```

Source adapters should use conservative request rates and include fixture-based
tests. Profile changes should remain backward compatible whenever possible.
