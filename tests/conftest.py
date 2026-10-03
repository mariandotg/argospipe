from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _isolate_notion_token(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    yield
