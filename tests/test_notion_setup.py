import pytest

from argospipe.config import Config
from argospipe.sources.notion_setup import (
    DuplicateNotionSourceError,
    NotionDatabaseIdError,
    add_notion_source,
    default_notion_source,
    parse_notion_database_id,
)

SAMPLE = "6e93ce47f5bc47a0bdbdb5f135f0a980"
SAMPLE_DASHED = "6e93ce47-f5bc-47a0-bdbd-b5f135f0a980"


@pytest.mark.parametrize(
    "raw",
    [
        SAMPLE,
        SAMPLE.upper(),
        SAMPLE_DASHED,
        f"https://www.notion.so/Argos-jobs-{SAMPLE}?v=abc123",
        f"https://notion.so/{SAMPLE_DASHED}",
        f"https://www.notion.com/p/workspace/{SAMPLE_DASHED}?v=view",
        f"https://app.notion.com/p/{SAMPLE}?pvs=204",
    ],
)
def test_parse_notion_database_id_accepts_common_forms(raw: str) -> None:
    assert parse_notion_database_id(raw) == SAMPLE


def test_parse_notion_database_id_rejects_garbage() -> None:
    with pytest.raises(NotionDatabaseIdError, match="Invalid Notion database"):
        parse_notion_database_id("not-a-database")


@pytest.mark.parametrize(
    "raw",
    [
        f"https://example.com/{SAMPLE}",
        f"https://www.notion.so/Argos-{SAMPLE}0",
        f"https://notion.so.evil.com/{SAMPLE}",
    ],
)
def test_parse_notion_database_id_rejects_foreign_or_malformed_urls(raw: str) -> None:
    with pytest.raises(NotionDatabaseIdError):
        parse_notion_database_id(raw)


def test_duplicate_check_normalizes_existing_ids() -> None:
    config = Config(sources=[default_notion_source(SAMPLE_DASHED.upper())])
    with pytest.raises(DuplicateNotionSourceError):
        add_notion_source(config, SAMPLE)
    assert len(config.sources) == 1
