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
