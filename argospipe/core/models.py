from typing import Literal

from pydantic import BaseModel, Field

from argospipe.config import Modality, Seniority
from argospipe.llm.schemas import MatchResult


class JobRecord(BaseModel):
    fingerprint: str
    company: str
    title: str
    location: str | None = None
    modality: Modality | None = None
    seniority: Seniority | None = None
    stack: list[str] = Field(default_factory=list)
    lang: str | None = None
    description: str | None = None
    text_hash: str | None = None
    url: str | None = None
    first_seen: str
    last_seen: str
    status: Literal["open", "closed"] = "open"


class Discard(BaseModel):
    fingerprint: str
    stage: Literal["prefiltered_out", "ranked_out"]
    reasons: list[str] = Field(default_factory=list)


class SourceStatus(BaseModel):
    name: str
    ok: bool
    error: str | None = None
    fetched: int


class RunMatch(BaseModel):
    job: JobRecord
    result: MatchResult


class RunResult(BaseModel):
    run_id: int
    started_at: str
    finished_at: str | None = None
    sources: list[SourceStatus] = Field(default_factory=list)
    new_count: int = 0
    discarded_count: int = 0
    missing_description_count: int = 0
    matched_count: int = 0
    matches: list[RunMatch] = Field(default_factory=list)
    discards: list[Discard] = Field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
