from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Find job offers, filter them, and match them against your CV.")
profile_app = typer.Typer(help="Manage your profile.")
app.add_typer(profile_app, name="profile")


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
