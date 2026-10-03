import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from anthropic.types import ToolUseBlock

from argospipe.config import CandidateProfile, Config, ModelPrice, Preferences
from argospipe.core.models import JobRecord
from argospipe.llm.anthropic import AnthropicProvider
from argospipe.llm.common import MAX_JOB_CHARS, LLMOutputError
from argospipe.llm.factory import make_provider
from argospipe.llm.openai import OpenAIProvider
from argospipe.llm.provider import Usage, cost_usd


class FakeMessages:
    def __init__(self, outputs: list[dict[str, object]]) -> None:
        self.outputs = outputs
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        output = self.outputs.pop(0)
        return SimpleNamespace(
            content=[
                ToolUseBlock(id="tool-1", name="submit_result", input=output, type="tool_use")
            ],
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
        )


class FakeClient:
    def __init__(self, outputs: list[dict[str, object]]) -> None:
        self.messages = FakeMessages(outputs)


def test_cost_uses_price_per_million_tokens() -> None:
    price = Config().pricing["claude-haiku-4-5"]
    assert cost_usd(Usage(tokens_in=3000, tokens_out=300), price) == pytest.approx(0.0045)


def test_invalid_profile_output_retries_and_sums_usage() -> None:
    client = FakeClient(
        [
            {"seniority": "expert"},
            {"roles": ["Engineer"], "seniority": "senior", "highlights": ["Built APIs"]},
        ]
    )
    provider = AnthropicProvider("test-model", client=client)  # type: ignore[arg-type]

    profile, usage = asyncio.run(provider.extract_profile("CV text"))

    assert profile.roles == ["Engineer"]
    assert usage == Usage(tokens_in=200, tokens_out=40)
    assert len(client.messages.calls) == 2
    call = client.messages.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": "submit_result"}
    assert call["tools"][0]["input_schema"]["title"] == "ProfileExtraction"
    assert call["messages"][0]["content"] == "CV text"


def test_invalid_output_twice_raises_with_total_usage() -> None:
    client = FakeClient([{"seniority": "expert"}, {"seniority": "expert"}])
    provider = AnthropicProvider("test-model", client=client)  # type: ignore[arg-type]

    with pytest.raises(LLMOutputError) as error:
        asyncio.run(provider.extract_profile("CV text"))

    assert error.value.usage == Usage(tokens_in=200, tokens_out=40)
    assert len(client.messages.calls) == 2


def test_match_trims_job_and_delimits_untrusted_posting() -> None:
    client = FakeClient([{"score": 75, "seniority_match": "match", "summary": "Good fit"}])
    provider = AnthropicProvider("test-model", client=client)  # type: ignore[arg-type]
    job = JobRecord(
        fingerprint="job-1",
        company="Acme",
        title="Engineer",
        description="A" * MAX_JOB_CHARS + "B" * 20,
        first_seen="2026-10-01",
        last_seen="2026-10-01",
    )

    result, usage = asyncio.run(
        provider.match(CandidateProfile(highlights=["Built APIs"]), Preferences(), job)
    )

    assert result.score == 75
    assert usage == Usage(tokens_in=100, tokens_out=20)
    call = client.messages.calls[0]
    content = call["messages"][0]["content"]
    posting = content.split("<job_posting>\n", 1)[1].split("\n</job_posting>", 1)[0]
    assert json.loads(posting)["description"] == "A" * MAX_JOB_CHARS
    assert "Built APIs" in content
    assert "untrusted" in call["system"]
    assert call["tools"][0]["input_schema"]["title"] == "MatchResult"


class FakeOpenAICompletions:
    def __init__(self, outputs: list[dict[str, object] | str]) -> None:
        self.outputs = outputs
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        output = self.outputs.pop(0)
        content = output if isinstance(output, str) else json.dumps(output)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
        )


class FakeOpenAIChat:
    def __init__(self, outputs: list[dict[str, object] | str]) -> None:
        self.completions = FakeOpenAICompletions(outputs)


class FakeOpenAIClient:
    def __init__(self, outputs: list[dict[str, object] | str]) -> None:
        self.chat = FakeOpenAIChat(outputs)


def test_make_provider_selects_openai_by_default() -> None:
    config = Config()
    provider = make_provider(config)
    assert isinstance(provider, OpenAIProvider)


def test_make_provider_selects_openai() -> None:
    config = Config(
        provider="openai",
        model="gpt-4.1-mini",
        pricing={"gpt-4.1-mini": ModelPrice(input_per_mtok=1.0, output_per_mtok=2.0)},
    )
    provider = make_provider(config)
    assert isinstance(provider, OpenAIProvider)


def test_make_provider_rejects_missing_pricing() -> None:
    config = Config(model="unpriced-model", pricing={})
    with pytest.raises(ValueError, match="No price for model"):
        make_provider(config)


def test_openai_invalid_profile_output_retries_and_sums_usage() -> None:
    pricing = {"test-model": ModelPrice(input_per_mtok=1.0, output_per_mtok=5.0)}
    client = FakeOpenAIClient(
        [
            {"seniority": "expert"},
            {"roles": ["Engineer"], "seniority": "senior", "highlights": ["Built APIs"]},
        ]
    )
    provider = OpenAIProvider("test-model", pricing=pricing, client=client)  # type: ignore[arg-type]

    profile, usage = asyncio.run(provider.extract_profile("CV text"))

    assert profile.roles == ["Engineer"]
    assert usage == Usage(tokens_in=200, tokens_out=40)
    assert len(client.chat.completions.calls) == 2


def test_openai_invalid_output_twice_raises_with_total_usage() -> None:
    pricing = {"test-model": ModelPrice(input_per_mtok=1.0, output_per_mtok=5.0)}
    client = FakeOpenAIClient([{"seniority": "expert"}, {"seniority": "expert"}])
    provider = OpenAIProvider("test-model", pricing=pricing, client=client)  # type: ignore[arg-type]

    with pytest.raises(LLMOutputError) as error:
        asyncio.run(provider.extract_profile("CV text"))

    assert error.value.usage == Usage(tokens_in=200, tokens_out=40)


def test_openai_missing_pricing_raises_before_call() -> None:
    client = FakeOpenAIClient([{"roles": ["Engineer"], "seniority": "senior", "highlights": []}])
    provider = OpenAIProvider("test-model", pricing={}, client=client)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="No price for model"):
        asyncio.run(provider.extract_profile("CV text"))

    assert client.chat.completions.calls == []


def test_openai_match_uses_structured_output_and_cost() -> None:
    pricing = {"test-model": ModelPrice(input_per_mtok=1.0, output_per_mtok=5.0)}
    client = FakeOpenAIClient([{"score": 75, "seniority_match": "match", "summary": "Good fit"}])
    provider = OpenAIProvider("test-model", pricing=pricing, client=client)  # type: ignore[arg-type]
    job = JobRecord(
        fingerprint="job-1",
        company="Acme",
        title="Engineer",
        description="Build APIs",
        first_seen="2026-10-01",
        last_seen="2026-10-01",
    )

    result, usage = asyncio.run(
        provider.match(CandidateProfile(highlights=["Built APIs"]), Preferences(), job)
    )

    assert result.score == 75
    assert usage == Usage(tokens_in=100, tokens_out=20)
    assert cost_usd(usage, pricing["test-model"]) == pytest.approx(0.0002)
    call = client.chat.completions.calls[0]
    schema = call["response_format"]["json_schema"]["schema"]
    assert schema["title"] == "MatchResult"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert "max_tokens" not in call
    assert "untrusted" in call["messages"][0]["content"]


def test_openai_malformed_json_twice_raises_with_total_usage() -> None:
    pricing = {"test-model": ModelPrice(input_per_mtok=1.0, output_per_mtok=5.0)}
    client = FakeOpenAIClient(["{not json", "still not json"])
    provider = OpenAIProvider("test-model", pricing=pricing, client=client)  # type: ignore[arg-type]

    with pytest.raises(LLMOutputError) as exc_info:
        asyncio.run(provider.extract_profile("cv"))

    assert exc_info.value.usage == Usage(tokens_in=200, tokens_out=40)
    assert len(client.chat.completions.calls) == 2
