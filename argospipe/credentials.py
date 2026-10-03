import os
from typing import Literal

import keyring

SERVICE_NAME = "argospipe"
KEYRING_USERNAME = "anthropic"
NOTION_KEYRING_USERNAME = "notion"
ENV_VAR = "ANTHROPIC_API_KEY"
NOTION_ENV_VAR = "NOTION_TOKEN"

ProviderName = Literal["anthropic", "openai"]

PROVIDER_ENV: dict[ProviderName, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}

_PROVIDER_KEYRING: dict[ProviderName, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
}


def get_api_key(provider: ProviderName = "anthropic") -> str | None:
    env = os.environ.get(PROVIDER_ENV[provider])
    if env:
        return env
    try:
        return keyring.get_password(SERVICE_NAME, _PROVIDER_KEYRING[provider])
    except Exception:
        return None


def save_api_key(key: str, provider: ProviderName = "anthropic") -> None:
    keyring.set_password(SERVICE_NAME, _PROVIDER_KEYRING[provider], key)


def get_notion_token() -> str | None:
    env = os.environ.get(NOTION_ENV_VAR)
    if env:
        return env
    try:
        return keyring.get_password(SERVICE_NAME, NOTION_KEYRING_USERNAME)
    except Exception:
        return None


def save_notion_token(token: str) -> None:
    keyring.set_password(SERVICE_NAME, NOTION_KEYRING_USERNAME, token)
