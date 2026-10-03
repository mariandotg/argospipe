from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import anthropic
import keyring.errors
import typer
from anthropic import AsyncAnthropic
from rich.console import Console
from rich.table import Table

from argospipe.config import (
    AtsSourceConfig,
    Config,
    Modality,
    Preferences,
    Profile,
    Seniority,
    config_path,
    load_config,
    load_profile,
    profile_path,
    save_config,
    save_profile,
)
from argospipe.credentials import ENV_VAR, get_api_key, save_api_key
from argospipe.llm.common import LLMOutputError
from argospipe.llm.factory import make_provider
from argospipe.llm.provider import LLMProvider
from argospipe.profile_import import import_profile
from argospipe.sources.companies import Region, companies_as_sources
from argospipe.sources.notion_setup import prompt_and_add_notion_source

MAX_API_KEY_ATTEMPTS = 3
MAX_PROMPT_ATTEMPTS = 3
REGION_CHOICES: tuple[Region, ...] = ("latam", "es", "eu-remote")
MODALITY_CHOICES: tuple[Modality, ...] = ("remote", "hybrid", "onsite")
SENIORITY_CHOICES: tuple[Seniority, ...] = (
    "intern",
    "junior",
    "semi-senior",
    "senior",
    "lead",
    "principal",
)

PromptFn = Callable[..., str]
ConfirmFn = Callable[..., bool]
ValidateApiKeyFn = Callable[[str, str], Awaitable[None]]
RunCommandFn = Callable[..., None]
ProviderFactory = Callable[[str], LLMProvider]
ConfigureNotionSourceFn = Callable[["InitWizardDeps", Config], None]


async def default_validate_api_key(key: str, model: str) -> None:
    client = AsyncAnthropic(api_key=key)
    await client.messages.create(
        model=model,
        max_tokens=1,
        messages=[{"role": "user", "content": "ping"}],
    )


def default_provider_factory(model: str) -> LLMProvider:
    config = load_config() if config_path().exists() else Config()
    return make_provider(config.model_copy(update={"model": model}))


@dataclass
class InitWizardDeps:
    prompt: PromptFn = typer.prompt
    confirm: ConfirmFn = typer.confirm
    validate_api_key: ValidateApiKeyFn = default_validate_api_key
    provider_factory: ProviderFactory = default_provider_factory
    run_command: RunCommandFn | None = None
    open_in_editor: Callable[[Path], None] | None = None
    configure_notion_source: ConfigureNotionSourceFn | None = None


def _open_profile_in_editor(path: Path) -> None:
    editor = os.environ.get("EDITOR", "vi")
    subprocess.run([*shlex.split(editor), str(path)], check=False)


def _parse_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _parse_modalities(value: str) -> list[Modality]:
    parts = _parse_csv(value.lower())
    if not parts:
        return []
    invalid = [p for p in parts if p not in MODALITY_CHOICES]
    if invalid:
        raise ValueError(f"Unknown modalities: {', '.join(invalid)}")
    return parts  # type: ignore[return-value]


def _parse_regions(value: str) -> list[Region]:
    parts = _parse_csv(value.lower())
    if not parts:
        raise ValueError("Select at least one region.")
    invalid = [p for p in parts if p not in REGION_CHOICES]
    if invalid:
        raise ValueError(f"Unknown regions: {', '.join(invalid)}")
    return parts  # type: ignore[return-value]


def _parse_seniority(value: str) -> Seniority | None:
    cleaned = value.strip().lower()
    if not cleaned:
        return None
    if cleaned not in SENIORITY_CHOICES:
        raise ValueError(f"Unknown seniority: {value}")
    return cleaned


def _parse_threshold(value: str) -> int:
    cleaned = value.strip()
    try:
        threshold = int(cleaned)
    except ValueError:
        raise ValueError("Threshold must be an integer between 0 and 100.") from None
    if not 0 <= threshold <= 100:
        raise ValueError("Threshold must be an integer between 0 and 100.")
    return threshold


def _is_invalid_api_key(exc: BaseException) -> bool:
    return isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)) or (
        isinstance(exc, anthropic.APIStatusError) and 400 <= exc.status_code < 500
    )


def _prompt_parsed[T](
    wizard: InitWizardDeps,
    message: str,
    parser: Callable[[str], T],
    *,
    default: str = "",
    hide_input: bool = False,
) -> T:
    for attempt in range(1, MAX_PROMPT_ATTEMPTS + 1):
        raw = wizard.prompt(message, default=default, hide_input=hide_input)
        try:
            return parser(raw)
        except ValueError as exc:
            if attempt >= MAX_PROMPT_ATTEMPTS:
                typer.echo(str(exc), err=True)
                raise typer.Exit(1) from exc
            typer.echo(str(exc), err=True)
    raise AssertionError("unreachable")


def _ats_key(source: AtsSourceConfig) -> tuple[str, str]:
    return source.ats, source.slug


def merge_company_sources(config: Config, regions: list[str]) -> None:
    existing = {
        _ats_key(source) for source in config.sources if isinstance(source, AtsSourceConfig)
    }
    for source in companies_as_sources(regions):
        key = _ats_key(source)
        if key not in existing:
            config.sources.append(source)
            existing.add(key)


def _print_profile(profile: Profile, console: Console) -> None:
    summary = Table(title="Extracted profile", show_header=False)
    summary.add_column("Field")
    summary.add_column("Value")
    for field, value in profile.profile.model_dump().items():
        summary.add_row(
            field.replace("_", " ").title(),
            ", ".join(value) if isinstance(value, list) else ("—" if value is None else str(value)),
        )
    console.print(summary)


def _confirm_overwrite(
    path: Path,
    force: bool,
    confirm: ConfirmFn,
    label: str,
) -> bool:
    if not path.exists() or force:
        return True
    return confirm(f"{label} already exists at {path}. Overwrite?", default=False)


def _maybe_configure_notion_source(wizard: InitWizardDeps, config: Config) -> None:
    if wizard.configure_notion_source is not None:
        wizard.configure_notion_source(wizard, config)
        return
    if not wizard.confirm("Do you have a Notion database with job offers?", default=False):
        return
    prompt_and_add_notion_source(config, wizard.prompt)


async def _ensure_api_key(wizard: InitWizardDeps, model: str) -> None:
    if get_api_key() is not None:
        return
    for attempt in range(1, MAX_API_KEY_ATTEMPTS + 1):
        key = wizard.prompt(
            "Anthropic API key",
            hide_input=True,
        )
        try:
            await wizard.validate_api_key(key, model)
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            typer.echo(
                "Network error validating API key. Check your connection and try again.",
                err=True,
            )
            raise typer.Exit(1) from exc
        except anthropic.APIError as exc:
            if _is_invalid_api_key(exc):
                if attempt >= MAX_API_KEY_ATTEMPTS:
                    typer.echo(f"Invalid API key after {MAX_API_KEY_ATTEMPTS} attempts.", err=True)
                    raise typer.Exit(1) from None
                typer.echo("Invalid API key. Try again.", err=True)
                continue
            typer.echo(f"API error validating key: {exc}", err=True)
            raise typer.Exit(1) from exc
        try:
            save_api_key(key)
        except keyring.errors.KeyringError:
            typer.echo(
                "Warning: could not store the API key in the system keyring. "
                f"Set {ENV_VAR} in your environment for future runs.",
                err=True,
            )
            os.environ[ENV_VAR] = key
        break


async def run_init_wizard(
    force: bool = False,
    deps: InitWizardDeps | None = None,
) -> bool:
    wizard = deps or InitWizardDeps()
    console = Console()

    if not _confirm_overwrite(profile_path(), force, wizard.confirm, "Profile"):
        raise typer.Exit(0)
    if not _confirm_overwrite(config_path(), force, wizard.confirm, "Config"):
        raise typer.Exit(0)

    config = load_config() if config_path().exists() else Config()
    await _ensure_api_key(wizard, config.model)

    cv_input = wizard.prompt("Path to your CV (PDF or text)")
    cv_path = Path(cv_input).expanduser()
    provider = wizard.provider_factory(config.model)

    try:
        profile = await import_profile(
            cv_path,
            provider,
            profile_path(),
            force=True,
        )
    except (OSError, ValueError, LLMOutputError) as exc:
        typer.echo(f"Profile import failed: {exc}", err=True)
        raise typer.Exit(1) from exc

    _print_profile(profile, console)

    if wizard.confirm("Open profile.yaml in your editor to review or edit?", default=False):
        editor = wizard.open_in_editor or _open_profile_in_editor
        editor(profile_path())
        profile = load_profile()

    modalities = _prompt_parsed(
        wizard,
        "Preferred modalities (comma-separated: remote, hybrid, onsite)",
        _parse_modalities,
        default="remote",
    )
    countries_raw = wizard.prompt(
        "Preferred countries (comma-separated ISO codes, empty for any)",
        default="",
    )
    min_seniority = _prompt_parsed(
        wizard,
        "Minimum seniority (intern, junior, semi-senior, senior, lead, principal; empty for any)",
        _parse_seniority,
        default="",
    )
    excluded_raw = wizard.prompt(
        "Companies to exclude (comma-separated names, empty for none)",
        default="",
    )
    threshold = _prompt_parsed(
        wizard,
        "Match score threshold",
        _parse_threshold,
        default="70",
    )

    profile.preferences = Preferences(
        modalities=modalities,
        countries=_parse_csv(countries_raw.upper()) if countries_raw.strip() else [],
        min_seniority=min_seniority,
        excluded_companies=_parse_csv(excluded_raw),
        threshold=threshold,
    )

    save_profile(profile)

    regions = _prompt_parsed(
        wizard,
        "Job board regions (comma-separated: latam, es, eu-remote)",
        _parse_regions,
        default="latam",
    )

    merge_company_sources(config, list(regions))
    _maybe_configure_notion_source(wizard, config)
    save_config(config)
    console.print(f"Added company sources for regions: {', '.join(regions)}")
    console.print(f"Profile saved to {profile_path()}")
    console.print(f"Config saved to {config_path()}")

    return wizard.confirm("Run argospipe now?", default=True)


def init_command(
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing files.")] = False,
    deps: InitWizardDeps | None = None,
) -> None:
    """Interactive setup: CV, profile, preferences, API key, and sources."""
    wizard = deps or InitWizardDeps()
    try:
        run_after = asyncio.run(run_init_wizard(force=force, deps=wizard))
    except typer.Exit:
        raise
    except (anthropic.APIError, OSError) as exc:
        typer.echo(f"Init failed: {exc}", err=True)
        raise typer.Exit(1) from exc

    if run_after:
        if wizard.run_command is None:
            from argospipe.cli import execute_run

            execute_run()
        else:
            wizard.run_command()
