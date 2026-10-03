from __future__ import annotations

import os
import re
from collections.abc import Callable
from urllib.parse import unquote, urlparse

import keyring.errors
import typer

from argospipe.config import Config, NotionFields, NotionSourceConfig
from argospipe.credentials import NOTION_ENV_VAR, get_notion_token, save_notion_token

PromptFn = Callable[..., str]

_HEX32 = re.compile(r"^[0-9a-fA-F]{32}$")
_NOTION_DOMAINS = ("notion.so", "notion.site", "notion.com")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class NotionDatabaseIdError(ValueError):
    """The database URL or id is not valid."""


class DuplicateNotionSourceError(ValueError):
    """That Notion database is already in config."""


def default_notion_fields() -> NotionFields:
    return NotionFields(
        title="Name",
        company="Company",
        url="Link",
        description="Description",
        location="Geo",
        posted_at="Found",
    )


def default_notion_source(database_id: str) -> NotionSourceConfig:
    return NotionSourceConfig(database_id=database_id, fields=default_notion_fields())


def parse_notion_database_id(raw: str) -> str:
    text = raw.strip()
    if not text:
        raise NotionDatabaseIdError(
            "Invalid Notion database id. Pass a 32-character id or a Notion URL containing it."
        )

    if _UUID.match(text):
        return text.replace("-", "").lower()
    if _HEX32.match(text):
        return text.lower()

    parsed = urlparse(text if "://" in text else f"https://{text}")
    host = (parsed.hostname or "").lower()
    if any(host == domain or host.endswith(f".{domain}") for domain in _NOTION_DOMAINS):
        segments = [part for part in unquote(parsed.path).split("/") if part]
        if segments:
            last = segments[-1]
            for candidate in (last.split("-")[-1], last.replace("-", "")):
                if _HEX32.match(candidate):
                    return candidate.lower()

    raise NotionDatabaseIdError(
        "Invalid Notion database id. Pass a 32-character id or a Notion URL containing it."
    )


def notion_database_configured(config: Config, database_id: str) -> bool:
    return any(
        isinstance(source, NotionSourceConfig)
        and source.database_id.replace("-", "").lower() == database_id
        for source in config.sources
    )


def ensure_notion_token(*, prompt: PromptFn) -> None:
    if get_notion_token() is not None:
        return
    token = prompt("Notion integration token", hide_input=True)
    try:
        save_notion_token(token)
    except keyring.errors.KeyringError:
        typer.echo(
            "Warning: could not store the Notion token in the system keyring. "
            f"Set {NOTION_ENV_VAR} in your environment for future runs.",
            err=True,
        )
        os.environ[NOTION_ENV_VAR] = token


def add_notion_source(
    config: Config,
    database_raw: str,
    *,
    prompt: PromptFn | None = None,
) -> str:
    database_id = parse_notion_database_id(database_raw)
    if notion_database_configured(config, database_id):
        raise DuplicateNotionSourceError(f"Notion database already configured: {database_id}")
    if prompt is not None:
        ensure_notion_token(prompt=prompt)
    config.sources.append(default_notion_source(database_id))
    return database_id


def prompt_and_add_notion_source(config: Config, wizard_prompt: PromptFn) -> None:
    while True:
        database_raw = wizard_prompt("Notion database URL or id (empty to skip)", default="")
        if not database_raw.strip():
            return
        try:
            add_notion_source(config, database_raw, prompt=wizard_prompt)
            return
        except (NotionDatabaseIdError, DuplicateNotionSourceError) as exc:
            typer.echo(str(exc), err=True)
