from typing import Any

from argospipe.config import Config
from argospipe.llm.anthropic import AnthropicProvider
from argospipe.llm.common import require_model_pricing
from argospipe.llm.openai import OpenAIProvider
from argospipe.llm.provider import LLMProvider


def make_provider(
    config: Config,
    *,
    anthropic_client: Any | None = None,
    openai_client: Any | None = None,
) -> LLMProvider:
    require_model_pricing(config.model, config.pricing)
    if config.provider == "anthropic":
        return AnthropicProvider(config.model, client=anthropic_client)
    if config.provider == "openai":
        return OpenAIProvider(config.model, pricing=config.pricing, client=openai_client)
    raise AssertionError(f"Unknown LLM provider: {config.provider!r}")
