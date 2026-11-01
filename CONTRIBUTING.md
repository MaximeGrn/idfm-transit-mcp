# Contributing

Use Python 3.11+ and `uv sync --locked`. Run `uv run ruff check .`,
`uv run ruff format --check .` and `uv run pytest -m 'not live'` before opening a PR.

Keep HTTP access, normalization, MCP and configuration separate. Add meaningful
regressions for API changes. Use synthetic fixtures; never commit credentials,
private journeys, assistant configurations or captured calendars. Live tests
are opt-in and must not run in CI.

Verify endpoint changes against current official documentation and, where
possible, an authenticated request. Explain incomplete coverage and pagination;
absence of data must not become an all-clear assertion. Personal automation,
notifications and preferences belong outside this repository.
