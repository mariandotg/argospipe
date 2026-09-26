# argospipe

Local CLI that finds job offers, drops the ones that do not fit, and matches the rest
against your CV with an LLM, explaining why. It runs on your machine with your own API key.

Status: v0.1 in progress. Spec: [`docs/spec.md`](docs/spec.md).

## Development

```bash
uv sync
uv run argospipe --help
uv run pytest
uv run ruff check && uv run ruff format --check && uv run mypy
```
