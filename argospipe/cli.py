from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from argospipe.config import (
    AtsSourceConfig,
    Config,
    FileSourceConfig,
    NotionSourceConfig,
    config_path,
    load_config,
    save_config,
)
from argospipe.sources.detect import UnsupportedURLError, detect

app = typer.Typer(help="Find job offers, filter them, and match them against your CV.")
profile_app = typer.Typer(help="Manage your profile.")
sources_app = typer.Typer(help="Manage job sources.")
app.add_typer(profile_app, name="profile")
app.add_typer(sources_app, name="sources")


@profile_app.command("import")
def profile_import(
    cv: Annotated[Path, typer.Argument(help="CV file (PDF or text).")],
) -> None:
    """Extract your profile from a CV into profile.yaml."""
    typer.echo("Not implemented yet.")
    raise typer.Exit(1)


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
