import hashlib
import os
from pathlib import Path
from typing import Annotated, Literal

import yaml
from platformdirs import user_data_dir
from pydantic import BaseModel, Field

APP_NAME = "argospipe"
HOME_ENV = "ARGOSPIPE_HOME"

Seniority = Literal["intern", "junior", "semi-senior", "senior", "lead", "principal"]
Modality = Literal["remote", "hybrid", "onsite"]


def data_dir() -> Path:
    override = os.environ.get(HOME_ENV)
    return Path(override) if override else Path(user_data_dir(APP_NAME))


def profile_path() -> Path:
    return data_dir() / "profile.yaml"


def config_path() -> Path:
    return data_dir() / "config.yaml"


def db_path() -> Path:
    return data_dir() / "argospipe.db"


def reports_dir() -> Path:
    return data_dir() / "reports"


class CandidateProfile(BaseModel):
    roles: list[str] = []
    seniority: Seniority | None = None
    years_experience: int | None = None
    stack: list[str] = []
    languages: list[str] = []
    highlights: list[str] = []


class Preferences(BaseModel):
    modalities: list[Modality] = []
    countries: list[str] = []
    min_seniority: Seniority | None = None
    excluded_companies: list[str] = []
    threshold: int = 70


class Profile(BaseModel):
    profile: CandidateProfile = CandidateProfile()
    preferences: Preferences = Preferences()


class NotionFields(BaseModel):
    title: str
    company: str
    location: str
    url: str
    description: str
    posted_at: str | None = None
    source_name: str | None = None


class NotionWriteback(BaseModel):
    score: str = "Argos score"
    status: str = "Argos estado"
    reason: str = "Argos motivo"
    summary: str = "Argos resumen"
    gaps: str = "Argos gaps"


class NotionSourceConfig(BaseModel):
    type: Literal["notion"] = "notion"
    database_id: str
    fields: NotionFields
    skip_statuses: list[str] = ["Closed"]
    writeback: NotionWriteback = NotionWriteback()


class FileSourceConfig(BaseModel):
    type: Literal["file"] = "file"
    path: Path


class AtsSourceConfig(BaseModel):
    type: Literal["ats"] = "ats"
    ats: Literal["greenhouse", "lever", "ashby"]
    slug: str
    name: str | None = None


SourceConfig = Annotated[
    NotionSourceConfig | FileSourceConfig | AtsSourceConfig,
    Field(discriminator="type"),
]


class ModelPrice(BaseModel):
    input_per_mtok: float
    output_per_mtok: float


class Config(BaseModel):
    model: str = "claude-haiku-4-5"
    max_matches_per_run: int = 15
    max_cost_per_run_usd: float = 1.0
    match_concurrency: int = 4
    close_after_days: int = 14
    pricing: dict[str, ModelPrice] = {
        "claude-haiku-4-5": ModelPrice(input_per_mtok=1.0, output_per_mtok=5.0),
    }
    sources: list[SourceConfig] = []


def _load_yaml[M: BaseModel](path: Path, model: type[M]) -> M:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return model.model_validate(data)


def _save_yaml(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = model.model_dump(mode="json")
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def load_profile(path: Path | None = None) -> Profile:
    return _load_yaml(path or profile_path(), Profile)


def save_profile(profile: Profile, path: Path | None = None) -> None:
    _save_yaml(path or profile_path(), profile)


def load_config(path: Path | None = None) -> Config:
    return _load_yaml(path or config_path(), Config)


def save_config(config: Config, path: Path | None = None) -> None:
    _save_yaml(path or config_path(), config)


def profile_version(path: Path | None = None) -> str:
    return hashlib.sha256((path or profile_path()).read_bytes()).hexdigest()[:16]
