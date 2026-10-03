import os
from typing import Literal

import keyring

SERVICE_NAME = "argospipe"
KEYRING_USERNAME = "anthropic"
ENV_VAR = "ANTHROPIC_API_KEY"

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
