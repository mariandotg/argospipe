import json
from typing import Any, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from argospipe.config import CandidateProfile, ModelPrice, Preferences
from argospipe.core.models import JobRecord
from argospipe.credentials import get_api_key
from argospipe.llm.common import (
    PROMPT_VERSION,
    LLMOutputError,
    build_match_content,
    load_prompt,
    require_model_pricing,
)
from argospipe.llm.provider import Usage
from argospipe.llm.schemas import MatchResult, ProfileExtraction

_Output = TypeVar("_Output", bound=BaseModel)


def strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """OpenAI strict mode: every object closed and every property required."""
    if schema.get("type") == "object" and "properties" in schema:
        schema["additionalProperties"] = False
        schema["required"] = list(schema["properties"])
    for value in schema.values():
        if isinstance(value, dict):
            strict_json_schema(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    strict_json_schema(item)
    return schema


class OpenAIProvider:
    def __init__(
        self,
        model: str,
        *,
        pricing: dict[str, ModelPrice] | None = None,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.model = model
        self._pricing = pricing
        self._client = client

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            api_key = get_api_key("openai")
            self._client = AsyncOpenAI(api_key=api_key) if api_key else AsyncOpenAI()
        return self._client

    def _ensure_pricing(self) -> None:
        if self._pricing is not None:
            require_model_pricing(self.model, self._pricing)

    async def _structured(
        self, prompt: str, content: str, schema: type[_Output]
    ) -> tuple[_Output, Usage]:
        self._ensure_pricing()
        usage = Usage(tokens_in=0, tokens_out=0)
        json_schema = strict_json_schema(schema.model_json_schema())
        for _ in range(2):
            response = await self.client.chat.completions.create(
                model=self.model,
                max_completion_tokens=2048,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": content},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "submit_result",
                        "schema": json_schema,
                        "strict": True,
                    },
                },
            )
            usage.tokens_in += response.usage.prompt_tokens if response.usage else 0
            usage.tokens_out += response.usage.completion_tokens if response.usage else 0
            raw = response.choices[0].message.content
            if raw is None:
                continue
            try:
                return schema.model_validate(json.loads(raw)), usage
            except (ValidationError, json.JSONDecodeError):
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
