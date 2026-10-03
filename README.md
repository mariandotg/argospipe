# argospipe

Local CLI that pulls job offers from your sources, drops the ones that clearly do not fit, ranks the rest, and uses an LLM to score each remaining offer against your profile—with reasons grounded in your CV. Everything runs on your machine with your own LLM API key; nothing is sent to a hosted argospipe service.

Full spec: [`docs/spec.md`](docs/spec.md).

## Install

Requires Python 3.12+ and an [OpenAI API key](https://platform.openai.com/) (default) or an [Anthropic API key](https://console.anthropic.com/) if you choose Anthropic during setup.

```bash
uv tool install git+https://github.com/mariandotg/argospipe
```

Set `OPENAI_API_KEY` or store the key in the OS keyring during `argospipe init` (`argospipe` / `openai`). For Anthropic, set `provider: anthropic` in `config.yaml` and use `ANTHROPIC_API_KEY` or keyring user `anthropic`.

## Quick start

```bash
argospipe init    # CV, profile, preferences, API key, and sources
argospipe run     # fetch, filter, match new offers, open the HTML report
```

## Commands

- **`init`** — Interactive setup: CV, profile, preferences, API key, and sources. Flag: `--force` (overwrite existing files).
- **`run`** — Read your sources, match new offers, and open the report. Flags: `--dry-run` (skip the LLM), `--json`, `--max-matches <int>` (cap LLM matches), `--no-open` (do not open the report).
- **`profile import <cv>`** — Extract your profile from a CV (PDF or text) into `profile.yaml`. Flags: `--force`, `--model <str>` (model; provider from config).
- **`sources add <url>`** — Add a Greenhouse, Lever, or Ashby job board from its careers URL. Flag: `--name <str>` (company name).
- **`sources list`** — List configured sources.
- **`eval --model <str>`** — Compare models against your own scores: agreement and cost. Repeat `--model` to compare several. Flags: `--pairs <path>` (default `eval/pairs.yaml`; start from `eval/pairs.example.yaml`), `--threshold <int>`, `--json`. Use **`eval sample`** to build `eval/pairs.yaml` from your local database (`--n`, `--out`, `--force`, `--seed`).
- **`schedule`** — Run argospipe every day (launchd on macOS, cron on Linux). Flags: `--at <HH:MM>` (default `09:00`), `--remove`.

## Sources

| Kind | How |
|------|-----|
| **Greenhouse, Lever, Ashby** | Public job-board APIs. Add with `argospipe sources add <careers-url>` (e.g. `boards.greenhouse.io`, `jobs.lever.co`, `jobs.ashbyhq.com`). |
| **CSV / JSON** | Add a `file` source in `config.yaml` (example below). Each row/object is a job record. **Required columns/keys:** `title`, `company`, `url`. **Optional:** `location`, `description`, `posted_at`, `source_name`, `source` (defaults to `file`), `external_id` (defaults to `url`). |
| **Notion** | A Notion database where your bot (or you) stores offers. Property mapping and setup: [`docs/notion-schema.md`](docs/notion-schema.md). Token via `argospipe sources add-notion` or `init` (keyring); `NOTION_TOKEN` still works and takes precedence. |

A `file` source in `config.yaml`:

```yaml
sources:
  - type: file
    path: /Users/you/jobs/offers.csv  # absolute; ~ is not expanded
```

## Privacy

- Runs locally. SQLite DB, config, and reports live under the app data directory from [`platformdirs`](https://github.com/tox-dev/platformdirs) (override with `ARGOSPIPE_HOME`).
- Your CV file is not stored—only the extracted profile in `profile.yaml`.
- API keys are read from `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` or the OS keyring (`argospipe` / `openai` or `anthropic`); they are never logged or written to config.
- Matching calls send only your profile, preferences, and the job text to the configured LLM provider—not your full CV on each run.

## Cost per run

Defaults (in `config.yaml` if you do not override):

| Setting | Default |
|---------|---------|
| Provider | `openai` |
| Model | `gpt-6-luna` |
| Max LLM matches per run | `15` |
| Per-run cost cap | `$1.00` |

Pricing table (USD per **million** tokens) for the default model: **$0.10** input, **$0.50** output for `gpt-6-luna`.

Anthropic alternative: set `provider: anthropic` and model `claude-haiku-4-5` (**$1** input, **$5** output per million tokens).

Each match uses on the order of **~3,000 input** and **~300 output** tokens (see [`docs/spec.md`](docs/spec.md)). With `gpt-6-luna`, cost per match: `(3000 × $0.10 + 300 × $0.50) / 1,000,000 ≈ $0.00045`.

With 15 matches, a full run costs about **$0.007**. Offers already matched in an earlier run are skipped and cost nothing. The run stops matching when accumulated cost reaches `max_cost_per_run_usd` (default $1), so the cap is a safety rail rather than the usual limit at default settings.

## License

MIT — see [`LICENSE`](LICENSE).

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md).
