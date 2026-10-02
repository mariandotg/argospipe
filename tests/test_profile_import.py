import asyncio
import re
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from typer.testing import CliRunner

from argospipe import cli
from argospipe.config import Config, ModelPrice, Preferences, load_profile, save_config
from argospipe.llm.provider import Usage
from argospipe.llm.schemas import ProfileExtraction
from argospipe.profile_import import import_profile, read_cv_text

runner = CliRunner()


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def extract_profile(self, cv_text: str) -> tuple[ProfileExtraction, Usage]:
        self.calls.append(cv_text)
        return (
            ProfileExtraction(
                roles=["Tech Lead"],
                seniority="lead",
                years_experience=10,
                stack=["Python"],
                languages=["English"],
                highlights=["Built APIs"],
            ),
            Usage(tokens_in=100, tokens_out=20),
        )


def _text_cv(tmp_path: Path) -> Path:
    path = tmp_path / "cv.txt"
    path.write_text("Tech Lead with 10 years of Python experience", encoding="utf-8")
    return path


def test_import_text_cv_writes_loadable_profile(tmp_path: Path) -> None:
    cv = _text_cv(tmp_path)
    out = tmp_path / "profile.yaml"
    provider = FakeProvider()

    profile = asyncio.run(import_profile(cv, provider, out, False))  # type: ignore[arg-type]

    assert provider.calls == [cv.read_text(encoding="utf-8")]
    assert load_profile(out) == profile
    assert profile.profile.roles == ["Tech Lead"]
    assert profile.preferences == Preferences()
    assert "preferences:" in out.read_text(encoding="utf-8")


def test_read_pdf_cv(tmp_path: Path) -> None:
    path = tmp_path / "cv.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 20 200 Td (Python engineer) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    with path.open("wb") as stream:
        writer.write(stream)

    assert "Python engineer" in read_cv_text(path)


def test_empty_cv_has_clear_error(tmp_path: Path) -> None:
    path = tmp_path / "cv.txt"
    path.write_text("  \n", encoding="utf-8")
    with pytest.raises(ValueError, match="No text found in CV"):
        read_cv_text(path)


def test_import_refuses_overwrite_before_provider_call(tmp_path: Path) -> None:
    cv = _text_cv(tmp_path)
    out = tmp_path / "profile.yaml"
    out.write_text("existing content", encoding="utf-8")
    provider = FakeProvider()

    with pytest.raises(FileExistsError, match="--force"):
        asyncio.run(import_profile(cv, provider, out, False))  # type: ignore[arg-type]

    assert provider.calls == []
    assert out.read_text(encoding="utf-8") == "existing content"


def test_cli_import_and_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGOSPIPE_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-test-key")
    save_config(
        Config(
            model="configured-model",
            pricing={"configured-model": ModelPrice(input_per_mtok=1.0, output_per_mtok=5.0)},
        )
    )
    cv = _text_cv(tmp_path)
    provider = FakeProvider()
    models: list[str] = []

    def fake_anthropic(model: str) -> FakeProvider:
        models.append(model)
        return provider

    monkeypatch.setattr(cli, "AnthropicProvider", fake_anthropic)
    first = runner.invoke(cli.app, ["profile", "import", str(cv)])
    output = re.sub(r"\x1b\[[0-9;]*m", "", first.output)
    assert first.exit_code == 0, first.output
    assert "profile.yaml" in output
    assert "Tech Lead" in output
    assert "100 input" in output
    assert "20 output" in output
    assert "$0.000200" in output
    assert "preferences" in output
    assert "secret-test-key" not in output
    assert models == ["configured-model"]
    assert load_profile().profile.roles == ["Tech Lead"]

    refused = runner.invoke(cli.app, ["profile", "import", str(cv)])
    assert refused.exit_code == 1
    assert "--force" in refused.output
    assert len(provider.calls) == 1

    forced = runner.invoke(cli.app, ["profile", "import", str(cv), "--force", "--model", "other"])
    assert forced.exit_code == 0, forced.output
    assert models == ["configured-model", "other"]
    assert len(provider.calls) == 2


def test_cli_missing_key_exits_without_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARGOSPIPE_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cv = _text_cv(tmp_path)
    provider = FakeProvider()
    monkeypatch.setattr(cli, "AnthropicProvider", lambda model: provider)

    result = runner.invoke(cli.app, ["profile", "import", str(cv)])

    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY" in result.output
    assert provider.calls == []
    assert not (tmp_path / "data" / "profile.yaml").exists()


def test_cli_corrupt_pdf_exits_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGOSPIPE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.7 not really a pdf")

    result = runner.invoke(cli.app, ["profile", "import", str(cv)])

    assert result.exit_code == 1
    assert "Profile import failed" in result.output
    assert not (tmp_path / "home" / "profile.yaml").exists()
