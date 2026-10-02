import os

import keyring

SERVICE_NAME = "argospipe"
KEYRING_USERNAME = "anthropic"
ENV_VAR = "ANTHROPIC_API_KEY"


def get_api_key() -> str | None:
    env = os.environ.get(ENV_VAR)
    if env:
        return env
    try:
        return keyring.get_password(SERVICE_NAME, KEYRING_USERNAME)
    except Exception:
        return None


def save_api_key(key: str) -> None:
    keyring.set_password(SERVICE_NAME, KEYRING_USERNAME, key)
