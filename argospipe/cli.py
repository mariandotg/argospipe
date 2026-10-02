import asyncio
import os
import re
from pathlib import Path
from typing import Annotated

import anthropic
import typer
import yaml
from pypdf.errors import PdfReadError
from rich.console import Console
from rich.table import Table

from argospipe import schedule as scheduler
from argospipe.config import (
    AtsSourceConfig,
    Config,
    FileSourceConfig,
    NotionSourceConfig,
    config_path,
    data_dir,
    load_config,
    load_profile,
    profile_path,
    save_config,
)
from argospipe.eval import EvalResult, load_pairs, run_eval
from argospipe.llm.anthropic import AnthropicProvider, LLMOutputError
from argospipe.llm.provider import Usage, cost_usd
from argospipe.profile_import import import_profile
from argospipe.sources.detect import UnsupportedURLError, detect

app = typer.Typer(help="Find job offers, filter them, and match them against your CV.")
profile_app = typer.Typer(help="Manage your profile.")
sources_app = typer.Typer(help="Manage job sources.")
app.add_typer(profile_app, name="profile")
app.add_typer(sources_app, name="sources")


@profile_app.command("import")
def profile_import(
    cv: Annotated[Path, typer.Argument(help="CV file (PDF or text).")],
    force: Annotated[bool, typer.Option("--force", help="Overwrite profile.yaml.")] = False,
    model: Annotated[str | None, typer.Option("--model", help="Anthropic model.")] = None,
) -> None:
    """Extract your profile from a CV into profile.yaml."""
    out_path = profile_path()
    if out_path.exists() and not force:
        typer.echo(f"Profile already exists: {out_path}. Use --force to overwrite it.", err=True)
        raise typer.Exit(1)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        typer.echo("ANTHROPIC_API_KEY is required to import a profile.", err=True)
        raise typer.Exit(1)

    try:
        config = load_config() if config_path().exists() else Config()
        selected_model = model or config.model
        price = config.pricing.get(selected_model)
        usage: Usage | None = None

        def record_usage(value: Usage) -> None:
            nonlocal usage
            usage = value

        profile = asyncio.run(
            import_profile(cv, AnthropicProvider(selected_model), out_path, force, record_usage)
        )
    except (OSError, ValueError, PdfReadError, anthropic.APIError, LLMOutputError) as exc:
        typer.echo(f"Profile import failed: {exc}", err=True)
        raise typer.Exit(1) from exc

    console = Console()
    console.print(f"Profile saved to {out_path}")
    summary = Table(title="Extracted profile", show_header=False)
    summary.add_column("Field")
    summary.add_column("Value")
    for field, value in profile.profile.model_dump().items():
        summary.add_row(
            field.replace("_", " ").title(),
            ", ".join(value) if isinstance(value, list) else ("—" if value is None else str(value)),
        )
    console.print(summary)
    if usage is not None:
        cost = (
            f"${cost_usd(usage, price):.6f}"
            if price is not None
            else "unavailable (no price for model)"
        )
        console.print(f"Tokens: {usage.tokens_in} input, {usage.tokens_out} output. Cost: {cost}")
    console.print("Edit profile.yaml to fill in preferences by hand.")


@app.command()
def run(
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Skip the LLM.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Print the result as JSON.")] = False,
    max_matches: Annotated[
        int | None, typer.Option("--max-matches", help="Cap LLM matches.")
    ] = None,
    no_open: Annotated[bool, typer.Option("--no-open", help="Do not open the report.")] = False,
    notion_writeback: Annotated[
        bool, typer.Option("--notion-writeback", help="Write score and reasons back to Notion.")
    ] = False,
) -> None:
    """Read your sources, match new offers, and open the report."""
    typer.echo("Not implemented yet.")
    raise typer.Exit(1)


@sources_app.command("add")
def sources_add(
    url: Annotated[str, typer.Argument(help="Careers URL for a supported ATS.")],
    name: Annotated[str | None, typer.Option("--name", help="Company name.")] = None,
) -> None:
    """Add a Greenhouse, Lever, or Ashby job board."""
    try:
        ats, slug = detect(url)
    except UnsupportedURLError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    path = config_path()
    config = load_config(path) if path.exists() else Config()
    if any(
        isinstance(source, AtsSourceConfig) and source.ats == ats and source.slug == slug
        for source in config.sources
    ):
        typer.echo(f"Source already configured: {ats}/{slug}", err=True)
        raise typer.Exit(code=1)

    config.sources.append(AtsSourceConfig(ats=ats, slug=slug, name=name))
    save_config(config, path)
    typer.echo(f"Added {name or slug} ({ats}: {slug}).")


@sources_app.command("list")
def sources_list() -> None:
    """List configured sources."""
    path = config_path()
    config = load_config(path) if path.exists() else Config()
    if not config.sources:
        typer.echo("No sources configured.")
        return

    table = Table("Type", "Identifier", "Name")
    for source in config.sources:
        if isinstance(source, AtsSourceConfig):
            table.add_row(source.ats, source.slug, source.name or "—")
        elif isinstance(source, FileSourceConfig):
            table.add_row("file", str(source.path), "—")
        elif isinstance(source, NotionSourceConfig):
            table.add_row("notion", source.database_id, "—")

    Console().print(table)


@app.command("eval")
def eval_command(
    model: Annotated[
        list[str], typer.Option("--model", help="Anthropic model to evaluate. Repeatable.")
    ],
    pairs: Annotated[Path, typer.Option("--pairs", help="Pairs file.")] = Path("eval/pairs.yaml"),
    json_output: Annotated[bool, typer.Option("--json", help="Print the result as JSON.")] = False,
    threshold: Annotated[
        int | None, typer.Option("--threshold", help="Score threshold for agreement.")
    ] = None,
) -> None:
    """Compare models against your own scores: agreement and cost."""
    if not pairs.exists():
        typer.echo(
            f"Pairs file not found: {pairs}. Copy eval/pairs.example.yaml to {pairs} and edit it.",
            err=True,
        )
        raise typer.Exit(1)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        typer.echo("ANTHROPIC_API_KEY is required to run the eval.", err=True)
        raise typer.Exit(1)

    try:
        loaded = load_pairs(pairs)
        config = load_config() if config_path().exists() else Config()
        default_profile = load_profile() if profile_path().exists() else None
    except (OSError, ValueError, yaml.YAMLError) as exc:
        typer.echo(f"Eval failed: {exc}", err=True)
        raise typer.Exit(1) from exc

    if threshold is None:
        threshold = default_profile.preferences.threshold if default_profile else 70
    result = asyncio.run(
        run_eval(
            loaded,
            model,
            AnthropicProvider,
            config,
            default_profile,
            threshold,
            config.match_concurrency,
        )
    )
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
    else:
        _print_eval(result)


def _print_eval(result: EvalResult) -> None:
    table = Table(
        "Model",
        "MAE",
        f"Agreement (>= {result.threshold})",
        "Failures",
        "Tokens in/out",
        "Cost",
        title=f"Eval: {result.pairs} pairs",
    )
    for m in result.models:
        table.add_row(
            m.model,
            "n/a" if m.mae is None else f"{m.mae:.1f}",
            "n/a" if m.agreement_pct is None else f"{m.agreement_pct:.0f}%",
            str(m.failures),
            f"{m.tokens_in}/{m.tokens_out}",
            "n/a" if m.cost_usd is None else f"${m.cost_usd:.4f}",
        )
    Console().print(table)


_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _parse_time(value: str) -> tuple[int, int]:
    match = _TIME_RE.match(value)
    if match and int(match[1]) < 24 and int(match[2]) < 60:
        return int(match[1]), int(match[2])
    typer.echo(f"Invalid time '{value}'. Use HH:MM (24-hour), for example 09:00.", err=True)
    raise typer.Exit(2)


@app.command()
def schedule(
    at: Annotated[str, typer.Option("--at", help="Daily run time, HH:MM.")] = "09:00",
    remove: Annotated[bool, typer.Option("--remove", help="Remove the schedule.")] = False,
) -> None:
    """Run argospipe every day (launchd on macOS, cron on Linux)."""
    hour, minute = _parse_time(at)
    executable = scheduler.resolve_executable()
    log_dir = data_dir()
    log_file = log_dir / scheduler.LOG_NAME

    try:
        if scheduler.current_platform() == "darwin":
            if remove:
                removed = scheduler.remove_macos()
                typer.echo("Removed the launchd agent." if removed else "No schedule installed.")
                return
            plist = scheduler.install_macos(executable, hour, minute, log_dir)
            typer.echo(f"Installed launchd agent {scheduler.LABEL} at {plist}.")
        elif scheduler.current_platform().startswith("linux"):
            if remove:
                removed = scheduler.remove_linux()
                typer.echo("Removed the crontab line." if removed else "No schedule installed.")
                return
            line = scheduler.install_linux(executable, hour, minute, log_dir)
            typer.echo(f"Installed crontab line:\n{line}")
        else:
            if remove:
                typer.echo("Nothing was installed on Windows. Delete the task in Task Scheduler.")
                return
            typer.echo(scheduler.windows_instructions(executable, hour, minute))
            return
    except (OSError, RuntimeError) as exc:
        typer.echo(f"Schedule failed: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Runs daily at {hour:02d}:{minute:02d}. Logs: {log_file}")
