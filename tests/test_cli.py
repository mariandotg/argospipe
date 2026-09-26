from typer.testing import CliRunner

from argospipe.cli import app

runner = CliRunner()


def test_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.output
    assert "profile" in result.output


def test_profile_help_lists_import() -> None:
    result = runner.invoke(app, ["profile", "--help"])
    assert result.exit_code == 0
    assert "import" in result.output
