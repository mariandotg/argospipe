import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from argospipe.config import NotionFields, NotionSourceConfig
from argospipe.sources.notion import NotionSource

FIXTURES = Path(__file__).parent / "fixtures" / "notion"


def _config(*, skip_statuses: list[str] | None = None) -> NotionSourceConfig:
    fields = NotionFields(
        title="Name",
        company="Company",
        url="Link",
        description="Description",
        location="Geo",
        posted_at="Found",
    )
    if skip_statuses is None:
        return NotionSourceConfig(database_id="database-123", fields=fields)
    return NotionSourceConfig(
        database_id="database-123",
        fields=fields,
        skip_statuses=skip_statuses,
    )


def test_maps_pages_paginates_and_skips_closed() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        fixture = "page_1.json" if len(requests) == 1 else "page_2.json"
        return httpx.Response(200, content=(FIXTURES / fixture).read_bytes())

    async def run() -> None:
        source = NotionSource(
            _config(), token="secret-token", transport=httpx.MockTransport(respond)
        )
        jobs = await source.fetch()

        assert [job.external_id for job in jobs] == ["page-open", "page-no-description"]
        first, second = jobs
        assert first.title == "Senior Engineer"
        assert first.company == "Acme"
        assert first.url == "https://example.com/jobs/1"
        assert first.description == "First part. Second part."
        assert first.location == "Remote"
        assert first.posted_at == "2026-09-29"
        assert first.source == "notion:database-123"
        assert first.source_name is None
        assert first.missing_description is False
        assert second.description is None
        assert second.location is None
        assert second.posted_at is None
        assert second.missing_description is True
        assert source.errors == [
            "page page-missing: missing required field(s): title, company, url"
        ]
        assert len(requests) == 2
        assert requests[0].method == "POST"
        assert requests[0].url == "https://api.notion.com/v1/databases/database-123/query"
        assert requests[0].headers["Authorization"] == "Bearer secret-token"
        assert requests[0].headers["Notion-Version"] == "2022-06-28"
        assert json.loads(requests[0].content) == {}
        assert json.loads(requests[1].content) == {"start_cursor": "cursor-2"}

    asyncio.run(run())


def test_since_filter_and_status_override() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        data = json.loads((FIXTURES / "page_1.json").read_bytes())
        data["has_more"] = False
        data["next_cursor"] = None
        return httpx.Response(200, json=data)

    async def run() -> None:
        source = NotionSource(
            _config(skip_statuses=[]),
            token="secret-token",
            since=datetime(2026, 9, 30, tzinfo=UTC),
            transport=httpx.MockTransport(respond),
        )
        jobs = await source.fetch()
        assert [job.external_id for job in jobs] == ["page-open", "page-closed"]
        assert json.loads(requests[0].content) == {
            "filter": {
                "timestamp": "last_edited_time",
                "last_edited_time": {"on_or_after": "2026-09-30T00:00:00+00:00"},
            }
        }

    asyncio.run(run())


def test_token_comes_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "environment-token")
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"results": [], "has_more": False, "next_cursor": None})

    source = NotionSource(_config(), transport=httpx.MockTransport(respond))
    assert asyncio.run(source.fetch()) == []
    assert requests[0].headers["Authorization"] == "Bearer environment-token"


def test_missing_token_fails_at_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    source = NotionSource(_config())
    with pytest.raises(ValueError, match="NOTION_TOKEN is required"):
        asyncio.run(source.fetch())


def test_retries_rate_limit_using_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    delays: list[float] = []
    requests: list[httpx.Request] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"results": [], "has_more": False, "next_cursor": None})

    monkeypatch.setattr("argospipe.sources.notion.asyncio.sleep", sleep)
    source = NotionSource(_config(), token="secret-token", transport=httpx.MockTransport(respond))
    assert asyncio.run(source.fetch()) == []
    assert len(requests) == 2
    assert delays == [2.0]


def test_naive_since_is_sent_as_utc() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"results": [], "has_more": False, "next_cursor": None})

    async def run() -> None:
        source = NotionSource(
            _config(skip_statuses=[]),
            token="secret-token",
            since=datetime(2026, 9, 30, 12, 0),
            transport=httpx.MockTransport(respond),
        )
        await source.fetch()
        on_or_after = json.loads(requests[0].content)["filter"]["last_edited_time"]["on_or_after"]
        assert on_or_after == "2026-09-30T12:00:00+00:00"

    asyncio.run(run())
