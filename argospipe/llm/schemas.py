from typing import Literal

from pydantic import BaseModel, Field

from argospipe.config import Seniority


class ProfileExtraction(BaseModel):
    roles: list[str] = Field(default_factory=list)
    seniority: Seniority | None = None
    years_experience: int | None = None
    stack: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)


class FitReason(BaseModel):
    reason: str
    evidence: str


class MatchResult(BaseModel):
    score: int = Field(ge=0, le=100)
    fit_reasons: list[FitReason] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    seniority_match: Literal["below", "match", "above"]
    summary: str
