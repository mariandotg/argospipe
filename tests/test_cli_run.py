import json
import re
import webbrowser
from pathlib import Path

import pytest
from typer.testing import CliRunner

from argospipe import config, pipeline
from argospipe.cli import app
from argospipe.config import NotionFields, NotionSourceConfig
from argospipe.core.models import RunResult
from argospipe.output.notion import NotionWritebackError, WritebackReport
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


def test_run_writes_report_and_opens_browser(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(home)
    monkeypatch.setattr(pipeline, "AnthropicProvider", lambda model: FakeProvider())
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 0, result.output
    reports = list((home / "reports").glob("*.html"))
    assert len(reports) == 1
    assert reports[0].read_text(encoding="utf-8")
    assert opened == [reports[0].as_uri()]


def test_run_no_open_skips_browser(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(home)
    monkeypatch.setattr(pipeline, "AnthropicProvider", lambda model: FakeProvider())
    monkeypatch.setattr(webbrowser, "open", lambda url: (_ for _ in ()).throw(AssertionError(url)))

    result = runner.invoke(app, ["run", "--no-open"])

    assert result.exit_code == 0, result.output
    assert list((home / "reports").glob("*.html"))


def test_run_json_writes_no_report(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(home)
    monkeypatch.setattr(pipeline, "AnthropicProvider", lambda model: FakeProvider())

    result = runner.invoke(app, ["run", "--json", "--no-open"])

    assert result.exit_code == 0, result.output
    assert not (home / "reports").exists()


def test_notion_writeback_only_with_flag_and_sources(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(home)
    monkeypatch.setattr(pipeline, "AnthropicProvider", lambda model: FakeProvider())
    calls: list[str] = []

    async def fake_writeback(result, notion_config, *, threshold, token=None, transport=None):
        calls.append(notion_config.database_id)
        return WritebackReport()

    monkeypatch.setattr("argospipe.cli.writeback", fake_writeback)

    without = runner.invoke(app, ["run", "--no-open"])
    assert without.exit_code == 0
    assert calls == []

    notion = NotionSourceConfig(
        database_id="db-test",
        fields=NotionFields(
            title="Name", company="Co", location="Geo", url="Link", description="Desc"
        ),
    )
    cfg = config.load_config()
    config.save_config(cfg.model_copy(update={"sources": [*cfg.sources, notion]}))

    with_flag = runner.invoke(app, ["run", "--no-open", "--notion-writeback"])
    assert with_flag.exit_code == 0, with_flag.output
    assert calls == ["db-test"]


def test_notion_writeback_skipped_on_dry_run(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(home)
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    calls: list[str] = []

    async def fake_writeback(*_args, **_kwargs):
        calls.append(True)
        return WritebackReport()

    monkeypatch.setattr("argospipe.cli.writeback", fake_writeback)

    result = runner.invoke(app, ["run", "--dry-run", "--notion-writeback", "--no-open"])

    assert result.exit_code == 0
    assert calls == []


def test_notion_writeback_schema_error_exits_one(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(home)
    monkeypatch.setattr(pipeline, "AnthropicProvider", lambda model: FakeProvider())
    notion = NotionSourceConfig(
        database_id="db-bad",
        fields=NotionFields(
            title="Name", company="Co", location="Geo", url="Link", description="Desc"
        ),
    )
    cfg = config.load_config()
    config.save_config(cfg.model_copy(update={"sources": [*cfg.sources, notion]}))

    async def schema_error(*_args, **_kwargs):
        raise NotionWritebackError("schema mismatch")

    monkeypatch.setattr("argospipe.cli.writeback", schema_error)

    result = runner.invoke(app, ["run", "--no-open", "--notion-writeback"])

    assert result.exit_code == 1
    assert "schema mismatch" in result.output
