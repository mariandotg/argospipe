from typing import TypeVar

from anthropic import AsyncAnthropic
from anthropic.types import ToolUseBlock
from pydantic import BaseModel, ValidationError

from argospipe.config import CandidateProfile, Preferences
from argospipe.core.models import JobRecord
from argospipe.credentials import get_api_key
from argospipe.llm.common import (
    PROMPT_VERSION,
    LLMOutputError,
    build_match_content,
    load_prompt,
)
from argospipe.llm.provider import Usage
from argospipe.llm.schemas import MatchResult, ProfileExtraction

_Output = TypeVar("_Output", bound=BaseModel)


class AnthropicProvider:
    def __init__(self, model: str, client: AsyncAnthropic | None = None) -> None:
        self.model = model
        self._client = client

    @property
    def client(self) -> AsyncAnthropic:
        if self._client is None:
            api_key = get_api_key("anthropic")
            self._client = AsyncAnthropic(api_key=api_key) if api_key else AsyncAnthropic()
        return self._client

    async def _structured(
        self, prompt: str, content: str, schema: type[_Output]
    ) -> tuple[_Output, Usage]:
        tool_name = "submit_result"
        usage = Usage(tokens_in=0, tokens_out=0)
        for _ in range(2):
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=prompt,
                messages=[{"role": "user", "content": content}],
                tools=[
                    {
                        "name": tool_name,
                        "description": "Return the requested structured result.",
                        "input_schema": schema.model_json_schema(),
                    }
                ],
                tool_choice={"type": "tool", "name": tool_name},
            )
            usage.tokens_in += response.usage.input_tokens
            usage.tokens_out += response.usage.output_tokens
            tool_output = next(
                (
                    block.input
                    for block in response.content
                    if isinstance(block, ToolUseBlock) and block.name == tool_name
                ),
                None,
            )
            try:
                return schema.model_validate(tool_output), usage
            except ValidationError:
                continue
        raise LLMOutputError(usage)

    async def extract_profile(self, cv_text: str) -> tuple[ProfileExtraction, Usage]:
        return await self._structured(load_prompt("profile_v1"), cv_text, ProfileExtraction)

    async def match(
        self,
        profile: CandidateProfile,
        preferences: Preferences,
        job: JobRecord,
    ) -> tuple[MatchResult, Usage]:
        content = build_match_content(profile, preferences, job)
        return await self._structured(load_prompt(PROMPT_VERSION), content, MatchResult)
