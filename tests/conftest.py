from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _isolate_credentials_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in ("NOTION_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    yield
