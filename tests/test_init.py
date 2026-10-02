from pathlib import Path

import anthropic
import httpx
import keyring
import keyring.backend
import pytest
from typer.testing import CliRunner

from argospipe import cli
from argospipe.config import (
    HOME_ENV,
    Profile,
    load_config,
    load_profile,
    profile_path,
    save_config,
    save_profile,
)
from argospipe.credentials import ENV_VAR, KEYRING_USERNAME, SERVICE_NAME, get_api_key, save_api_key
from argospipe.init_wizard import InitWizardDeps, merge_company_sources
from argospipe.llm.provider import Usage
from argospipe.llm.schemas import ProfileExtraction
from argospipe.sources.companies import companies_as_sources

runner = CliRunner()


class MemoryKeyring(keyring.backend.KeyringBackend):
    priority = 1

    def __init__(self) -> None:
        self.passwords: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self.passwords[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self.passwords.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        self.passwords.pop((service, username), None)


class FakeProvider:
    def __init__(self, model: str) -> None:
        self.model = model

    async def extract_profile(self, cv_text: str) -> tuple[ProfileExtraction, Usage]:
        return (
            ProfileExtraction(
                roles=["Backend Engineer"],
                seniority="senior",
                years_experience=8,
                stack=["Python", "PostgreSQL"],
                languages=["English", "Spanish"],
                highlights=["Scaled APIs"],
            ),
            Usage(tokens_in=50, tokens_out=10),
        )


@pytest.fixture
def memory_keyring() -> MemoryKeyring:
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    return backend


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory_keyring: MemoryKeyring) -> Path:
    memory_keyring.passwords.clear()
    data = tmp_path / "data"
    monkeypatch.setenv(HOME_ENV, str(data))
    monkeypatch.delenv(ENV_VAR, raising=False)
    return data


def _cv(tmp_path: Path) -> Path:
    path = tmp_path / "cv.txt"
    path.write_text("Senior backend engineer with Python experience", encoding="utf-8")
    return path


def _happy_input(cv: Path, *, api_key: str = "sk-good", run_now: str = "n") -> str:
    return "\n".join(
        [
            str(cv),
            "n",
            "",
            "",
            "",
            "",
            "",
            api_key,
            "",
            run_now,
        ]
    )


async def _noop_validate(key: str, model: str) -> None:
    return None


def _api_error() -> anthropic.APIError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIError("invalid key", request, body=None)


def _patch_init_deps(monkeypatch: pytest.MonkeyPatch, deps: InitWizardDeps) -> None:
    import argospipe.init_wizard as init_wizard

    def wrapped(force: bool = False) -> None:
        init_wizard.init_command(force=force, deps=deps)

    monkeypatch.setattr(cli, "init_command", wrapped)


def test_get_api_key_prefers_env_over_keyring(
    monkeypatch: pytest.MonkeyPatch, memory_keyring: MemoryKeyring
) -> None:
    save_api_key("keyring-secret")
    monkeypatch.setenv(ENV_VAR, "env-secret")

    assert get_api_key() == "env-secret"


def test_happy_path_writes_profile_config_and_keyring(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cv = _cv(tmp_path)
    run_calls: list[str] = []

    async def validate(key: str, model: str) -> None:
        assert model == "claude-haiku-4-5"
        if key != "sk-good":
            raise _api_error()

    deps = InitWizardDeps(
        validate_api_key=validate,
        provider_factory=lambda model: FakeProvider(model),
        run_command=lambda: run_calls.append("run"),
    )
    _patch_init_deps(monkeypatch, deps)

    result = runner.invoke(cli.app, ["init"], input=_happy_input(cv))

    assert result.exit_code == 0, result.output
    profile = load_profile()
    assert profile.profile.roles == ["Backend Engineer"]
    assert profile.preferences.modalities == ["remote"]
    assert profile.preferences.threshold == 70

    config = load_config()
    latam_names = {s.name for s in companies_as_sources(["latam"])}
    configured = {
        (s.ats, s.slug) for s in config.sources if hasattr(s, "ats") and hasattr(s, "slug")
    }
    assert configured.issuperset({(s.ats, s.slug) for s in companies_as_sources(["latam"])})
    assert len(configured) == len(latam_names)

    assert keyring.get_password(SERVICE_NAME, KEYRING_USERNAME) == "sk-good"
    assert "sk-good" not in result.output
    assert run_calls == []


def test_invalid_api_key_retried_then_succeeds(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cv = _cv(tmp_path)
    attempts: list[str] = []

    async def validate(key: str, model: str) -> None:
        attempts.append(key)
        if key != "sk-good":
            raise _api_error()

    deps = InitWizardDeps(
        validate_api_key=validate,
        provider_factory=lambda model: FakeProvider(model),
        run_command=lambda: None,
    )
    _patch_init_deps(monkeypatch, deps)
    input_lines = "\n".join(
        [str(cv), "n", "", "", "", "", "", "bad-1", "bad-2", "sk-good", "es", "n"]
    )
    result = runner.invoke(cli.app, ["init"], input=input_lines)

    assert result.exit_code == 0, result.output
    assert attempts == ["bad-1", "bad-2", "sk-good"]
    assert keyring.get_password(SERVICE_NAME, KEYRING_USERNAME) == "sk-good"


def test_existing_profile_not_overwritten_without_confirmation(tmp_path: Path, home: Path) -> None:
    existing = Profile()
    existing.profile.roles = ["Keep Me"]
    save_profile(existing)
    original = profile_path().read_text(encoding="utf-8")

    result = runner.invoke(
        cli.app,
        ["init"],
        input="\n".join(["n"]),
    )

    assert result.exit_code == 0, result.output
    assert profile_path().read_text(encoding="utf-8") == original
    assert load_profile().profile.roles == ["Keep Me"]


def test_force_skips_overwrite_confirmation(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from argospipe.config import Config

    cv = _cv(tmp_path)
    save_profile(Profile())
    save_config(Config())

    deps = InitWizardDeps(
        provider_factory=lambda model: FakeProvider(model),
        validate_api_key=_noop_validate,
        run_command=lambda: None,
    )
    _patch_init_deps(monkeypatch, deps)

    result = runner.invoke(
        cli.app,
        ["init", "--force"],
        input=_happy_input(cv, api_key="sk-good"),
    )

    assert result.exit_code == 0, result.output
    assert load_profile().profile.roles == ["Backend Engineer"]


def test_merge_company_sources_keeps_existing_and_avoids_duplicates() -> None:
    from argospipe.config import AtsSourceConfig, Config

    first = companies_as_sources(["latam"])[0]
    config = Config(sources=[first])
    merge_company_sources(config, ["latam", "es"])
    keys = [(s.ats, s.slug) for s in config.sources if isinstance(s, AtsSourceConfig)]
    assert len(keys) == len(set(keys))
    assert len(config.sources) > 1


def test_offers_run_when_confirmed(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cv = _cv(tmp_path)
    run_calls: list[str] = []

    deps = InitWizardDeps(
        provider_factory=lambda model: FakeProvider(model),
        validate_api_key=_noop_validate,
        run_command=lambda: run_calls.append("ran"),
    )
    _patch_init_deps(monkeypatch, deps)

    result = runner.invoke(
        cli.app,
        ["init"],
        input=_happy_input(cv, api_key="sk-good", run_now="y"),
    )

    assert result.exit_code == 0, result.output
    assert run_calls == ["ran"]
