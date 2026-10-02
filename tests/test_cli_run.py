import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from argospipe import config, pipeline
from argospipe.cli import app
from argospipe.core.models import RunResult
from tests.test_pipeline import JOBS, PROFILE, FakeProvider

runner = CliRunner()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return tmp_path


def configure(home: Path) -> None:
    jobs_file = home / "jobs.json"
    jobs_file.write_text(
        json.dumps([job.model_dump(exclude={"missing_description"}) for job in JOBS])
    )
    config.save_config(config.Config(sources=[config.FileSourceConfig(path=jobs_file)]))
    config.save_profile(PROFILE)


def test_run_without_profile_suggests_profile_import(home: Path) -> None:
    result = runner.invoke(app, ["run"])

    assert result.exit_code == 1
    assert "argospipe profile import" in result.output


def test_run_without_config_suggests_sources_add(home: Path) -> None:
    config.save_profile(PROFILE)

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 1
    assert "argospipe sources add" in result.output


def test_run_json_with_max_matches_override(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(home)
    provider = FakeProvider()
    monkeypatch.setattr(pipeline, "AnthropicProvider", lambda model: provider)

    result = runner.invoke(app, ["run", "--json", "--max-matches", "1", "--no-open"])

    assert result.exit_code == 0, result.output
    run_result = RunResult.model_validate_json(result.output)
    assert run_result.matched_count == 1
    assert len(provider.calls) == 1
    assert run_result.sources[0].fetched == 5


def test_run_prints_summary(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(home)
    monkeypatch.setattr(pipeline, "AnthropicProvider", lambda model: FakeProvider())

    result = runner.invoke(app, ["run", "--notion-writeback"])

    assert result.exit_code == 0, result.output
    output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "Matched: 2" in output
    assert "Recommended" in output
    assert "Acme fits." in output


def test_dry_run_needs_no_api_key(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(home)
    monkeypatch.delenv("ANTHROPIC_API_KEY")

    def no_provider(model: str) -> None:
        raise AssertionError("dry run must not build a provider")

    monkeypatch.setattr(pipeline, "AnthropicProvider", no_provider)

    result = runner.invoke(app, ["run", "--dry-run", "--json"])

    assert result.exit_code == 0, result.output
    run_result = RunResult.model_validate_json(result.output)
    assert run_result.matches == []
    assert run_result.new_count == 5
