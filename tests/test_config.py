from pathlib import Path

import pytest

from argospipe import config as cfg


def test_profile_roundtrip(tmp_path: Path) -> None:
    profile = cfg.Profile(
        profile=cfg.CandidateProfile(
            roles=["Tech Lead"],
            seniority="lead",
            years_experience=10,
            stack=["python", "java"],
            languages=["es", "en"],
            highlights=["Migró un monolito a Spring Boot 3"],
        ),
        preferences=cfg.Preferences(
            modalities=["remote"],
            countries=["AR"],
            min_seniority="senior",
            excluded_companies=["Acme"],
            threshold=75,
        ),
    )
    path = tmp_path / "profile.yaml"
    cfg.save_profile(profile, path)
    assert cfg.load_profile(path) == profile


def test_config_roundtrip(tmp_path: Path) -> None:
    config = cfg.Config(
        model="claude-haiku-4-5",
        max_matches_per_run=10,
        sources=[
            cfg.NotionSourceConfig(
                database_id="abc123",
                fields=cfg.NotionFields(
                    title="Puesto",
                    company="Empresa",
                    location="Ubicación",
                    url="Link",
                    description="Descripción",
                    posted_at="Fecha",
                    source_name="Fuente",
                ),
            ),
            cfg.FileSourceConfig(path=Path("jobs.csv")),
        ],
    )
    path = tmp_path / "config.yaml"
    cfg.save_config(config, path)
    assert cfg.load_config(path) == config


def test_profile_version_changes_on_edit(tmp_path: Path) -> None:
    path = tmp_path / "profile.yaml"
    cfg.save_profile(cfg.Profile(), path)
    before = cfg.profile_version(path)
    cfg.save_profile(cfg.Profile(preferences=cfg.Preferences(threshold=80)), path)
    assert cfg.profile_version(path) != before


def test_data_dir_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(cfg.HOME_ENV, str(tmp_path))
    assert cfg.db_path() == tmp_path / "argospipe.db"


def test_fresh_config_defaults_to_openai_gpt_6_luna() -> None:
    config = cfg.Config()
    assert config.provider == "openai"
    assert config.model == "gpt-6-luna"
    price = config.pricing["gpt-6-luna"]
    assert price.input_per_mtok == 0.10
    assert price.output_per_mtok == 0.50


def test_legacy_config_without_provider_infers_anthropic_from_claude_model(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("model: claude-haiku-4-5\n", encoding="utf-8")
    config = cfg.load_config(path)
    assert config.provider == "anthropic"
    assert config.model == "claude-haiku-4-5"


def test_legacy_config_without_provider_or_model_defaults_openai(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("max_matches_per_run: 5\n", encoding="utf-8")
    config = cfg.load_config(path)
    assert config.provider == "openai"
    assert config.model == "gpt-6-luna"


def test_explicit_provider_not_overridden_by_legacy_inference(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "provider: anthropic\nmodel: gpt-6-luna\n",
        encoding="utf-8",
    )
    config = cfg.load_config(path)
    assert config.provider == "anthropic"
    assert config.model == "gpt-6-luna"
