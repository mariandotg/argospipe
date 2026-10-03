from pathlib import Path

import keyring
import pytest
from typer.testing import CliRunner

from argospipe import config
from argospipe.cli import app
from argospipe.credentials import NOTION_KEYRING_USERNAME, SERVICE_NAME, save_notion_token
from argospipe.sources.notion_setup import default_notion_fields
from tests.test_notion_credentials import MemoryKeyring

runner = CliRunner()

DATABASE_ID = "6e93ce47f5bc47a0bdbdb5f135f0a980"


def test_sources_add_creates_config_with_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path))
    result = runner.invoke(app, ["sources", "add", "boards.greenhouse.io/acme", "--name", "Acme"])

    assert result.exit_code == 0
    stored = config.load_config()
    assert stored.model == config.Config().model
    assert stored.sources == [config.AtsSourceConfig(ats="greenhouse", slug="acme", name="Acme")]


def test_sources_add_preserves_existing_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path))
    existing = config.Config(sources=[config.FileSourceConfig(path=Path("jobs.csv"))])
    config.save_config(existing)

    result = runner.invoke(app, ["sources", "add", "jobs.lever.co/acme"])

    assert result.exit_code == 0
    stored = config.load_config()
    assert stored.sources == [
        config.FileSourceConfig(path=Path("jobs.csv")),
        config.AtsSourceConfig(ats="lever", slug="acme"),
    ]


def test_sources_list_prints_configured_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path))
    config.save_config(
        config.Config(sources=[config.AtsSourceConfig(ats="ashby", slug="acme", name="Acme")])
    )

    result = runner.invoke(app, ["sources", "list"])

    assert result.exit_code == 0
    assert "ashby" in result.output
    assert "acme" in result.output
    assert "Acme" in result.output


def test_sources_add_refuses_duplicate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path))
    result = runner.invoke(app, ["sources", "add", "jobs.lever.co/acme"])
    assert result.exit_code == 0
    before = config.config_path().read_text(encoding="utf-8")

    duplicate = runner.invoke(app, ["sources", "add", "https://jobs.lever.co/acme/jobs/123"])

    assert duplicate.exit_code == 1
    assert "already configured" in duplicate.output
    assert config.config_path().read_text(encoding="utf-8") == before


def test_sources_add_unsupported_url_does_not_write_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path))

    result = runner.invoke(app, ["sources", "add", "https://example.com/acme"])

    assert result.exit_code == 1
    assert "Unsupported careers URL" in result.output
    assert not config.config_path().exists()


def test_sources_add_notion_creates_config_with_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    try:
        monkeypatch.setenv(config.HOME_ENV, str(tmp_path))
        result = runner.invoke(
            app,
            ["sources", "add-notion", DATABASE_ID],
            input="integration-token\n",
        )

        assert result.exit_code == 0, result.output
        stored = config.load_config()
        assert len(stored.sources) == 1
        notion = stored.sources[0]
        assert isinstance(notion, config.NotionSourceConfig)
        assert notion.database_id == DATABASE_ID
        assert notion.fields == default_notion_fields()
        assert backend.get_password(SERVICE_NAME, NOTION_KEYRING_USERNAME) == "integration-token"
        assert "integration-token" not in result.output
    finally:
        keyring.set_keyring(previous)


def test_sources_add_notion_accepts_url_with_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    try:
        save_notion_token("existing")
        monkeypatch.setenv(config.HOME_ENV, str(tmp_path))

        url = f"https://www.notion.so/jobs-{DATABASE_ID}?v=abc"
        result = runner.invoke(app, ["sources", "add-notion", url])
    finally:
        keyring.set_keyring(previous)

    assert result.exit_code == 0, result.output
    notion = config.load_config().sources[0]
    assert isinstance(notion, config.NotionSourceConfig)
    assert notion.database_id == DATABASE_ID


def test_sources_add_notion_invalid_id_exits_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path))

    result = runner.invoke(app, ["sources", "add-notion", "garbage-id"])

    assert result.exit_code == 1
    assert "Invalid Notion database" in result.output
    assert not config.config_path().exists()


def test_sources_add_notion_refuses_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    try:
        save_notion_token("existing")
        monkeypatch.setenv(config.HOME_ENV, str(tmp_path))
        runner.invoke(app, ["sources", "add-notion", DATABASE_ID])
        before = config.config_path().read_text(encoding="utf-8")

        duplicate = runner.invoke(
            app,
            ["sources", "add-notion", f"https://notion.so/{DATABASE_ID}"],
        )
    finally:
        keyring.set_keyring(previous)

    assert duplicate.exit_code == 1
    assert "already configured" in duplicate.output
    assert config.config_path().read_text(encoding="utf-8") == before


def test_sources_add_notion_skips_token_prompt_when_keyring_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    try:
        save_notion_token("existing")
        monkeypatch.setenv(config.HOME_ENV, str(tmp_path))

        result = runner.invoke(app, ["sources", "add-notion", DATABASE_ID])
    finally:
        keyring.set_keyring(previous)

    assert result.exit_code == 0, result.output
    assert result.output.strip().startswith("Added Notion")
