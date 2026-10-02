import asyncio
import os
import sqlite3
from pathlib import Path
from typing import Annotated

import anthropic
import typer
from pypdf.errors import PdfReadError
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from argospipe import pipeline
from argospipe.config import (
    AtsSourceConfig,
    Config,
    FileSourceConfig,
    NotionSourceConfig,
    config_path,
    load_config,
    load_profile,
    profile_path,
    save_config,
)
from argospipe.core.models import RunResult
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
    if not profile_path().exists():
        typer.echo(
            f"No profile found at {profile_path()}. Run `argospipe profile import <cv>` first.",
            err=True,
        )
        raise typer.Exit(1)
    if not config_path().exists():
        typer.echo(
            f"No config found at {config_path()}. Run `argospipe sources add <url>` first.",
            err=True,
        )
        raise typer.Exit(1)
    if not dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        typer.echo(
            "ANTHROPIC_API_KEY is required to match offers. Use --dry-run to skip the LLM.",
            err=True,
        )
        raise typer.Exit(1)

    try:
        config = load_config()
        profile = load_profile()
        result = asyncio.run(
            pipeline.run(config, profile, dry_run=dry_run, max_matches=max_matches)
        )
    except (OSError, ValueError, sqlite3.Error, anthropic.AnthropicError) as exc:
        typer.echo(f"Run failed: {exc}", err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return
    _print_run_summary(result, profile.preferences.threshold, dry_run)


def _print_run_summary(result: RunResult, threshold: int, dry_run: bool) -> None:
    console = Console()
    sources = Table("Source", "Status", "Fetched", title=f"Run {result.run_id}")
    for source in result.sources:
        status = "ok" if source.ok else f"[red]failed[/red]: {escape(source.error or '')}"
        sources.add_row(source.name, status, str(source.fetched))
    console.print(sources)

    console.print(
        f"New: {result.new_count} · Discarded: {result.discarded_count} · "
        f"Missing description: {result.missing_description_count} · "
        f"Matched: {result.matched_count} · Failed: {result.failed_count}"
    )
    recommended = [match for match in result.matches if match.result.score >= threshold]
    if recommended:
        table = Table("Score", "Title", "Company", "Summary", title="Recommended")
        for match in recommended:
            table.add_row(
                str(match.result.score), match.job.title, match.job.company, match.result.summary
            )
        console.print(table)
    if dry_run:
        console.print("Dry run: no LLM calls.")
    console.print(
        f"Tokens: {result.tokens_in} input, {result.tokens_out} output. "
        f"Cost: ${result.cost_usd:.4f}"
    )
    if result.cost_cap_reached:
        console.print("[yellow]Cost cap reached: matching stopped early.[/yellow]")


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
