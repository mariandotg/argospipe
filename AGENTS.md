# argospipe — agent guide

Spec and task list: `docs/spec.md`. It is the source of truth. Part 2 "Decisiones fijas"
is binding; if something is not defined there, ask before you invent it.

## Commands

```bash
uv sync
uv run pytest
uv run ruff check && uv run ruff format --check && uv run mypy
```

Every PR: tests green, `ruff` and `mypy` clean.

## Rules

- Unit tests make no network calls. Use recorded fixtures in `tests/fixtures/`.
- Never add scrapers for LinkedIn or for sites whose terms forbid it.
- Never log or persist the API key. The CV is never stored; only the extracted profile.
- Schema changes go in a new numbered file in `argospipe/db/migrations/`. Never edit an applied one.
- Set `ARGOSPIPE_HOME` to redirect the data dir (tests use it).
- No AI attribution in commits or PRs: no `Co-Authored-By` trailers, no "Generated with" lines.
