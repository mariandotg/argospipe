from collections.abc import Iterator

import keyring
import keyring.backend
import pytest

from argospipe.credentials import NOTION_ENV_VAR, get_notion_token, save_notion_token


class MemoryKeyring(keyring.backend.KeyringBackend):
    priority = 1

    def __init__(self) -> None:
        self.passwords: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self.passwords[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self.passwords.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        self.passwords.pop((service, username), None)


@pytest.fixture
def memory_keyring() -> Iterator[MemoryKeyring]:
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


def test_get_notion_token_prefers_env_over_keyring(
    monkeypatch: pytest.MonkeyPatch, memory_keyring: MemoryKeyring
) -> None:
    save_notion_token("keyring-token")
    monkeypatch.setenv(NOTION_ENV_VAR, "env-token")

    assert get_notion_token() == "env-token"


def test_get_notion_token_from_keyring(memory_keyring: MemoryKeyring) -> None:
    save_notion_token("stored-token")

    assert get_notion_token() == "stored-token"


def test_get_notion_token_missing_when_unset(memory_keyring: MemoryKeyring) -> None:
    assert get_notion_token() is None


def test_get_notion_token_keyring_error_returns_none(
    monkeypatch: pytest.MonkeyPatch, memory_keyring: MemoryKeyring
) -> None:
    save_notion_token("stored-token")

    def boom(service: str, username: str) -> str | None:
        raise RuntimeError("backend down")

    monkeypatch.setattr(keyring, "get_password", boom)

    assert get_notion_token() is None
