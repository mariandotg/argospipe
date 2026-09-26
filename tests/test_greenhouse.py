import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest

from argospipe.sources.greenhouse import GreenhouseSource, _description
from argospipe.sources.http import SourceHTTPClient, SourceNotFoundError

FIXTURES = Path(__file__).parent / "fixtures" / "ats" / "greenhouse"


@pytest.mark.parametrize("slug", ["datadog", "gitlab"])
def test_recorded_board_maps_jobs(slug: str) -> None:
    fixture = (FIXTURES / f"{slug}.json").read_bytes()
    original = json.loads(fixture)
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=fixture)

    async def run() -> None:
        async with SourceHTTPClient(transport=httpx.MockTransport(respond)) as client:
            source = GreenhouseSource(slug, client=client)
            jobs = await source.fetch()

        assert len(jobs) == 3
        for raw, job in zip(original["jobs"], jobs, strict=True):
            assert job.external_id == str(raw["id"])
            assert job.title == raw["title"]
            assert job.company == raw["company_name"]
            assert job.source_name == raw["company_name"]
            assert job.url == raw["absolute_url"]
            assert job.location == raw["location"]["name"]
            assert job.posted_at == (raw["first_published"] or raw["updated_at"])
            assert job.source == f"greenhouse:{slug}"
            assert job.description
            assert not re.search(r"<[^>]+>|&[a-zA-Z]+;|&#\d+;", job.description)
        assert requests[0].url.path == f"/v1/boards/{slug}/jobs"
        assert requests[0].url.params["content"] == "true"

    asyncio.run(run())


def test_empty_board_returns_no_jobs() -> None:
    fixture = (FIXTURES / "empty.json").read_bytes()

    async def run() -> None:
        async with SourceHTTPClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=fixture))
        ) as client:
            assert await GreenhouseSource("test", client=client).fetch() == []

    asyncio.run(run())


def test_unknown_slug_raises_source_not_found() -> None:
    fixture = (FIXTURES / "not_found.json").read_bytes()

    async def run() -> None:
        async with SourceHTTPClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(404, content=fixture))
        ) as client:
            with pytest.raises(SourceNotFoundError):
                await GreenhouseSource("argospipe-nope-xyz", client=client).fetch()

    asyncio.run(run())


def test_description_keeps_paragraph_breaks() -> None:
    assert _description("&lt;p&gt;First &amp;amp; second&lt;/p&gt;&lt;p&gt;Next&lt;/p&gt;") == (
        "First & second\nNext"
    )
