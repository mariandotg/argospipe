import asyncio
from collections.abc import Iterator
from pathlib import Path

import anthropic
import httpx
import keyring
import keyring.backend
import keyring.errors
import pytest
from typer.testing import CliRunner

from argospipe import cli
from argospipe.config import (
    HOME_ENV,
    AtsSourceConfig,
    Config,
    FileSourceConfig,
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


@pytest.fixture
def memory_keyring() -> Iterator[MemoryKeyring]:
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory_keyring: MemoryKeyring) -> Path:
    memory_keyring.passwords.clear()
    data = tmp_path / "data"
    monkeypatch.setenv(HOME_ENV, str(data))
    monkeypatch.setenv(ENV_VAR, "")
    monkeypatch.delenv(ENV_VAR)
    return data


def _cv(tmp_path: Path) -> Path:
    path = tmp_path / "cv.txt"
    path.write_text("Senior backend engineer with Python experience", encoding="utf-8")
    return path


def _happy_input(cv: Path, *, api_key: str = "sk-good", run_now: str = "n") -> str:
    return "\n".join(
        [
            api_key,
            str(cv),
            "n",
            "",
            "",
            "",
            "",
            "",
            "",
            run_now,
        ]
    )


async def _noop_validate(key: str, model: str) -> None:
    return None


def _auth_error() -> anthropic.AuthenticationError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(401, request=request)
    return anthropic.AuthenticationError("invalid key", response=response, body=None)


def _connection_error() -> anthropic.APIConnectionError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIConnectionError(request=request)


def _patch_init_deps(monkeypatch: pytest.MonkeyPatch, deps: InitWizardDeps) -> None:
    import argospipe.init_wizard as init_wizard

    def wrapped(force: bool = False) -> None:
        init_wizard.init_command(force=force, deps=deps)

    monkeypatch.setattr(cli, "init_command", wrapped)


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


class OrderTrackingProvider:
    def __init__(self, model: str) -> None:
        self.model = model
        self.events: list[str] = []

    async def extract_profile(self, cv_text: str) -> tuple[ProfileExtraction, Usage]:
        self.events.append("extract_profile")
        return (
            ProfileExtraction(
                roles=["Backend Engineer"],
                seniority="senior",
                years_experience=8,
                stack=["Python"],
                languages=["English"],
                highlights=[],
            ),
            Usage(tokens_in=1, tokens_out=1),
        )


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
            raise _auth_error()

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
            raise _auth_error()

    deps = InitWizardDeps(
        validate_api_key=validate,
        provider_factory=lambda model: FakeProvider(model),
        run_command=lambda: None,
    )
    _patch_init_deps(monkeypatch, deps)
    input_lines = "\n".join(
        ["bad-1", "bad-2", "sk-good", str(cv), "n", "", "", "", "", "", "es", "n"]
    )
    result = runner.invoke(cli.app, ["init"], input=input_lines)

    assert result.exit_code == 0, result.output
    assert attempts == ["bad-1", "bad-2", "sk-good"]
    assert keyring.get_password(SERVICE_NAME, KEYRING_USERNAME) == "sk-good"


def test_api_key_max_attempts_exits_without_saving(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cv = _cv(tmp_path)

    async def validate(key: str, model: str) -> None:
        raise _auth_error()

    deps = InitWizardDeps(
        validate_api_key=validate,
        provider_factory=lambda model: FakeProvider(model),
        run_command=lambda: None,
    )
    _patch_init_deps(monkeypatch, deps)
    input_lines = "\n".join(["bad-1", "bad-2", "bad-3", str(cv)])
    result = runner.invoke(cli.app, ["init"], input=input_lines)

    assert result.exit_code == 1, result.output
    assert "Invalid API key after 3 attempts" in result.output
    assert keyring.get_password(SERVICE_NAME, KEYRING_USERNAME) is None


def test_network_error_during_api_key_validation_exits_without_saving(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cv = _cv(tmp_path)

    async def validate(key: str, model: str) -> None:
        raise _connection_error()

    deps = InitWizardDeps(
        validate_api_key=validate,
        provider_factory=lambda model: FakeProvider(model),
        run_command=lambda: None,
    )
    _patch_init_deps(monkeypatch, deps)
    result = runner.invoke(cli.app, ["init"], input="\n".join(["sk-any", str(cv)]))

    assert result.exit_code == 1, result.output
    assert "Network error" in result.output
    assert "Invalid API key" not in result.output
    assert keyring.get_password(SERVICE_NAME, KEYRING_USERNAME) is None


def test_keyring_failure_sets_env_and_continues(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cv = _cv(tmp_path)

    def failing_save(key: str) -> None:
        raise keyring.errors.KeyringError("no backend")

    monkeypatch.setattr("argospipe.init_wizard.save_api_key", failing_save)

    deps = InitWizardDeps(
        validate_api_key=_noop_validate,
        provider_factory=lambda model: FakeProvider(model),
        run_command=lambda: None,
    )
    _patch_init_deps(monkeypatch, deps)
    result = runner.invoke(cli.app, ["init"], input=_happy_input(cv, api_key="sk-good"))

    assert result.exit_code == 0, result.output
    assert "could not store" in result.output.lower()
    assert "sk-good" not in result.output
    assert get_api_key() == "sk-good"
    assert load_profile().profile.roles == ["Backend Engineer"]


def test_api_key_prompted_before_profile_extraction(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cv = _cv(tmp_path)
    provider_holder: list[OrderTrackingProvider] = []
    validation_happened = False

    def factory(model: str) -> OrderTrackingProvider:
        provider = OrderTrackingProvider(model)
        provider_holder.append(provider)
        return provider

    async def validate(key: str, model: str) -> None:
        nonlocal validation_happened
        validation_happened = True
        assert provider_holder == []

    deps = InitWizardDeps(
        validate_api_key=validate,
        provider_factory=factory,
        run_command=lambda: None,
    )
    _patch_init_deps(monkeypatch, deps)
    result = runner.invoke(cli.app, ["init"], input=_happy_input(cv, api_key="sk-good"))

    assert result.exit_code == 0, result.output
    assert validation_happened
    assert provider_holder[0].events == ["extract_profile"]


def test_run_now_without_injected_run_command_avoids_nested_event_loop(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cv = _cv(tmp_path)
    executed: list[str] = []

    def fake_execute_run() -> None:
        asyncio.run(asyncio.sleep(0))
        executed.append("ran")

    monkeypatch.setattr(cli, "execute_run", fake_execute_run)

    deps = InitWizardDeps(
        validate_api_key=_noop_validate,
        provider_factory=lambda model: FakeProvider(model),
        run_command=None,
    )
    _patch_init_deps(monkeypatch, deps)

    result = runner.invoke(
        cli.app,
        ["init"],
        input=_happy_input(cv, api_key="sk-good", run_now="y"),
    )

    assert result.exit_code == 0, result.output
    assert executed == ["ran"]


def test_invalid_modality_reprompts_then_succeeds(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cv = _cv(tmp_path)
    deps = InitWizardDeps(
        validate_api_key=_noop_validate,
        provider_factory=lambda model: FakeProvider(model),
        run_command=lambda: None,
    )
    _patch_init_deps(monkeypatch, deps)
    lines = [
        "sk-good",
        str(cv),
        "n",
        "teleport",
        "remote",
        "",
        "",
        "",
        "",
        "",
        "n",
    ]
    result = runner.invoke(cli.app, ["init"], input="\n".join(lines))

    assert result.exit_code == 0, result.output
    assert "Unknown modalities" in result.output
    assert load_profile().preferences.modalities == ["remote"]


def test_invalid_seniority_and_region_reprompt_then_succeed(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cv = _cv(tmp_path)
    deps = InitWizardDeps(
        validate_api_key=_noop_validate,
        provider_factory=lambda model: FakeProvider(model),
        run_command=lambda: None,
    )
    _patch_init_deps(monkeypatch, deps)
    lines = ["sk-good", str(cv), "n", "", "", "wizard", "senior", "", "", "mars", "es", "n"]
    result = runner.invoke(cli.app, ["init"], input="\n".join(lines))

    assert result.exit_code == 0, result.output
    assert "Unknown seniority" in result.output
    assert "Unknown regions" in result.output
    assert load_profile().preferences.min_seniority == "senior"


def test_invalid_threshold_three_times_exits(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cv = _cv(tmp_path)
    deps = InitWizardDeps(
        validate_api_key=_noop_validate,
        provider_factory=lambda model: FakeProvider(model),
        run_command=lambda: None,
    )
    _patch_init_deps(monkeypatch, deps)
    lines = [
        "sk-good",
        str(cv),
        "n",
        "",
        "",
        "",
        "",
        "abc",
        "200",
        "999",
        "n",
    ]
    result = runner.invoke(cli.app, ["init"], input="\n".join(lines))

    assert result.exit_code == 1, result.output
    assert "Threshold must be an integer" in result.output


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


def test_force_overwrites_existing_config_yaml(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cv = _cv(tmp_path)
    config = Config()
    config.model = "custom-model"
    save_config(config)

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
    assert load_config().model == "custom-model"
    assert len(load_config().sources) > 0


def test_init_keeps_existing_sources_in_config(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cv = _cv(tmp_path)
    custom = FileSourceConfig(path=Path("/tmp/jobs.json"))
    config = Config(sources=[custom])
    save_config(config)

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
    loaded = load_config()
    assert any(
        isinstance(s, FileSourceConfig) and s.path == Path("/tmp/jobs.json") for s in loaded.sources
    )
    assert any(isinstance(s, AtsSourceConfig) for s in loaded.sources)


def test_merge_company_sources_keeps_existing_and_avoids_duplicates() -> None:
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
