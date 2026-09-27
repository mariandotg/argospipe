from typing import Protocol

from pydantic import BaseModel

from argospipe.config import CandidateProfile, ModelPrice, Preferences
from argospipe.core.models import JobRecord
from argospipe.llm.schemas import MatchResult, ProfileExtraction


class Usage(BaseModel):
    tokens_in: int
    tokens_out: int


class LLMProvider(Protocol):
    async def extract_profile(self, cv_text: str) -> tuple[ProfileExtraction, Usage]: ...

    async def match(
        self,
        profile: CandidateProfile,
        preferences: Preferences,
        job: JobRecord,
    ) -> tuple[MatchResult, Usage]: ...


def cost_usd(usage: Usage, price: ModelPrice) -> float:
    raise NotImplementedError
