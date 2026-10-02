from pathlib import Path

import pytest
from typer.testing import CliRunner

from argospipe import config
from argospipe.cli import app

runner = CliRunner()


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
