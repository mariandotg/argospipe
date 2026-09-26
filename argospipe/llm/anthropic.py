from argospipe.config import CandidateProfile, Preferences
from argospipe.core.models import JobRecord
from argospipe.llm.provider import LLMProvider, Usage
from argospipe.llm.schemas import MatchResult, ProfileExtraction


class AnthropicProvider(LLMProvider):
    def __init__(self, model: str) -> None:
        raise NotImplementedError

    async def extract_profile(self, cv_text: str) -> tuple[ProfileExtraction, Usage]:
        raise NotImplementedError

    async def match(
        self,
        profile: CandidateProfile,
        preferences: Preferences,
        job: JobRecord,
    ) -> tuple[MatchResult, Usage]:
        raise NotImplementedError
