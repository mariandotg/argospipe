# Contributing to argospipe

Spec and task list: [`docs/spec.md`](docs/spec.md). Part 2 **Decisiones fijas** is binding; if something is not defined there, ask before inventing it.

## Development setup

```bash
uv sync
uv run argospipe --help
```

Verify before opening a PR:

```bash
uv build && uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest
```

Tests, lint, and typecheck must be green on every PR.

## Project rules

- **Unit tests must not use the network.** Record HTTP responses under `tests/fixtures/` and replay them (see existing ATS tests).
- **No scrapers for LinkedIn** or for sites whose terms forbid automated access.
- **Never log or persist the Anthropic API key.** The CV is never stored; only the extracted profile in `profile.yaml`.
- **Database schema changes:** add a new numbered SQL file in `argospipe/db/migrations/`. Never edit a migration that has already shipped.
- **Data directory:** set `ARGOSPIPE_HOME` to redirect the app data dir (tests rely on this).

## Adding a source adapter

1. **Read the contract** in `argospipe/sources/base.py`: implement the `Source` protocol (`name` + async `fetch() -> list[RawJob]`) and map external data to `RawJob`.
2. **Follow an existing adapter.** [`argospipe/sources/greenhouse.py`](argospipe/sources/greenhouse.py) is a good reference: public HTTP API, HTML cleanup, stable `external_id`, and `source` set to `greenhouse:<slug>`. Tests live in [`tests/test_greenhouse.py`](tests/test_greenhouse.py) with fixtures in `tests/fixtures/ats/greenhouse/`.
3. **Register the adapter** in `build_source()` in [`argospipe/pipeline.py`](argospipe/pipeline.py) so `config.yaml` sources of your type are instantiated at run time.
4. **Add a config variant** in [`argospipe/config.py`](argospipe/config.py) (extend the `SourceConfig` discriminated union if you introduce a new `type`).
5. **Wire CLI or init** if users need to add the source interactively (see `sources add` and `init_wizard` for ATS and file/Notion patterns).
6. **Tests:** add recorded fixtures and a test module that asserts mapping, error handling, and edge cases without network I/O.

For ATS boards, URL detection lives in [`argospipe/sources/detect.py`](argospipe/sources/detect.py); new hosts belong there and in `sources add` validation.
