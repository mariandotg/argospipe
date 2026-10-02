import json
from importlib.resources import files
from typing import TypeVar

from anthropic import AsyncAnthropic
from anthropic.types import ToolUseBlock
from pydantic import BaseModel, ValidationError

from argospipe.config import CandidateProfile, Preferences
from argospipe.core.models import JobRecord
from argospipe.llm.provider import Usage
from argospipe.llm.schemas import MatchResult, ProfileExtraction

PROMPT_VERSION = "match_v1"
MAX_JOB_CHARS = 6000
_Output = TypeVar("_Output", bound=BaseModel)


class LLMOutputError(Exception):
    def __init__(self, usage: Usage) -> None:
        super().__init__("LLM tool output failed validation twice")
        self.usage = usage


def _prompt(name: str) -> str:
    return files("argospipe.llm").joinpath("prompts", f"{name}.md").read_text(encoding="utf-8")


class AnthropicProvider:
    def __init__(self, model: str, client: AsyncAnthropic | None = None) -> None:
        self.model = model
        self._client = client

    @property
    def client(self) -> AsyncAnthropic:
        if self._client is None:
            self._client = AsyncAnthropic()
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
        return await self._structured(_prompt("profile_v1"), cv_text, ProfileExtraction)

    async def match(
        self,
        profile: CandidateProfile,
        preferences: Preferences,
        job: JobRecord,
    ) -> tuple[MatchResult, Usage]:
        description = (job.description or "")[:MAX_JOB_CHARS]
        posting = {
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "description": description,
        }
        posting_text = json.dumps(posting, ensure_ascii=False).replace(
            "</job_posting>", "&lt;/job_posting&gt;"
        )
        content = (
            f"<profile>\n{profile.model_dump_json()}\n</profile>\n"
            f"<preferences>\n{preferences.model_dump_json()}\n</preferences>\n"
            f"<job_posting>\n{posting_text}\n</job_posting>"
        )
        return await self._structured(_prompt(PROMPT_VERSION), content, MatchResult)
