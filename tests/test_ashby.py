import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest

from argospipe.sources.ashby import AshbySource
from argospipe.sources.http import SourceHTTPClient, SourceNotFoundError

FIXTURES = Path(__file__).parent / "fixtures" / "ats" / "ashby"


def _expected_location(job: dict) -> str:
    parts: list[str] = []
    primary = job.get("location")
    if isinstance(primary, str) and primary.strip():
        parts.append(primary.strip())
    for entry in job.get("secondaryLocations") or []:
        loc = entry.get("location")
        if isinstance(loc, str) and loc.strip():
            parts.append(loc.strip())
    return "; ".join(parts)


@pytest.mark.parametrize("slug", ["nubank", "supabase"])
def test_recorded_board_maps_jobs(slug: str) -> None:
    fixture = (FIXTURES / f"{slug}.json").read_bytes()
    original = json.loads(fixture)
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=fixture)

    async def run() -> None:
        async with SourceHTTPClient(transport=httpx.MockTransport(respond)) as client:
            source = AshbySource(slug, client=client)
            jobs = await source.fetch()

        assert len(jobs) == 3
        for raw, job in zip(original["jobs"], jobs, strict=True):
            assert job.external_id == raw["id"]
            assert job.title == raw["title"]
            assert job.company == slug
            assert job.source_name == slug
            assert job.url == raw["jobUrl"]
            assert job.location == _expected_location(raw)
            published = raw.get("publishedAt")
            assert job.posted_at == (published[:10] if published else None)
            assert job.source == f"ashby:{slug}"
            assert job.description
            assert not re.search(r"<[^>]+>|&[a-zA-Z]+;|&#\d+;", job.description)
        assert requests[0].url.path == f"/posting-api/job-board/{slug}"

    asyncio.run(run())


def test_secondary_locations_joined() -> None:
    fixture = (FIXTURES / "nubank.json").read_bytes()
    original = json.loads(fixture)

    async def run() -> None:
        async with SourceHTTPClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=fixture))
        ) as client:
            jobs = await AshbySource("nubank", client=client).fetch()

        first = jobs[0]
        assert first.location == "Miami; Virginia; Durham"
        assert first.location == _expected_location(original["jobs"][0])

    asyncio.run(run())


def test_empty_board_returns_no_jobs() -> None:
    fixture = (FIXTURES / "empty.json").read_bytes()

    async def run() -> None:
        async with SourceHTTPClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=fixture))
        ) as client:
            assert await AshbySource("deel", client=client).fetch() == []

    asyncio.run(run())


def test_unknown_slug_raises_source_not_found() -> None:
    fixture = (FIXTURES / "not_found.json").read_bytes()

    async def run() -> None:
        async with SourceHTTPClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(404, content=fixture))
        ) as client:
            with pytest.raises(SourceNotFoundError):
                await AshbySource("argospipe-nope-xyz", client=client).fetch()

    asyncio.run(run())


def test_explicit_name_overrides_company() -> None:
    fixture = (FIXTURES / "supabase.json").read_bytes()

    async def run() -> None:
        transport = httpx.MockTransport(lambda _: httpx.Response(200, content=fixture))
        async with SourceHTTPClient(transport=transport) as client:
            jobs = await AshbySource("supabase", name="Supabase Inc.", client=client).fetch()

        assert {job.company for job in jobs} == {"Supabase Inc."}
        assert {job.source_name for job in jobs} == {"Supabase Inc."}

    asyncio.run(run())
