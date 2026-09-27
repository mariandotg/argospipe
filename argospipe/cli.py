import asyncio
import os
from pathlib import Path
from typing import Annotated

import anthropic
import typer
from pypdf.errors import PdfReadError
from rich.console import Console
from rich.table import Table

from argospipe.config import Config, config_path, load_config, profile_path
from argospipe.llm.anthropic import AnthropicProvider, LLMOutputError
from argospipe.llm.provider import Usage, cost_usd
from argospipe.profile_import import import_profile

app = typer.Typer(help="Find job offers, filter them, and match them against your CV.")
profile_app = typer.Typer(help="Manage your profile.")
app.add_typer(profile_app, name="profile")


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
