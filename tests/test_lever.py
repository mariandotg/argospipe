import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from argospipe.sources.http import SourceHTTPClient, SourceNotFoundError
from argospipe.sources.lever import LeverSource

FIXTURES = Path(__file__).parent / "fixtures" / "ats" / "lever"


def _posted_at(created_at_ms: int) -> str:
    return datetime.fromtimestamp(created_at_ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def _expected_location(categories: dict[str, object]) -> str | None:
    location = categories.get("location")
    if location:
        return str(location)
    all_locations = categories.get("allLocations") or []
    if all_locations:
        return ", ".join(str(loc) for loc in all_locations)
    return None


@pytest.mark.parametrize("slug", ["dlocal", "yuno"])
def test_recorded_board_maps_jobs(slug: str) -> None:
    fixture = (FIXTURES / f"{slug}.json").read_bytes()
    original = json.loads(fixture)
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=fixture)

    async def run() -> None:
        async with SourceHTTPClient(transport=httpx.MockTransport(respond)) as client:
            source = LeverSource(slug, client=client)
            jobs = await source.fetch()

        assert len(jobs) == 3
        for raw, job in zip(original, jobs, strict=True):
            categories = raw.get("categories") or {}
            assert job.external_id == raw["id"]
            assert job.title == raw["text"]
            assert job.company == slug
            assert job.source_name == slug
            assert job.url == raw["hostedUrl"]
            assert job.location == _expected_location(categories)
            assert job.posted_at == _posted_at(raw["createdAt"])
            assert job.source == f"lever:{slug}"
            if raw["id"] == "554f1f2e-a12f-4df8-b537-2e812f2d025a":
                assert job.missing_description
            else:
                assert job.description
                assert not job.missing_description
                assert not re.search(r"<[^>]+>|&[a-zA-Z]+;|&#\d+;", job.description)
        assert requests[0].url.path == f"/v0/postings/{slug}"
        assert requests[0].url.params["mode"] == "json"

    asyncio.run(run())


def test_empty_board_returns_no_jobs() -> None:
    fixture = (FIXTURES / "empty.json").read_bytes()

    async def run() -> None:
        async with SourceHTTPClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=fixture))
        ) as client:
            assert await LeverSource("demo", client=client).fetch() == []

    asyncio.run(run())


def test_unknown_slug_raises_source_not_found() -> None:
    fixture = (FIXTURES / "not_found.json").read_bytes()

    async def run() -> None:
        async with SourceHTTPClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(404, content=fixture))
        ) as client:
            with pytest.raises(SourceNotFoundError):
                await LeverSource("argospipe-nope-xyz", client=client).fetch()

    asyncio.run(run())


def test_explicit_name_overrides_company() -> None:
    fixture = (FIXTURES / "dlocal.json").read_bytes()

    async def run() -> None:
        transport = httpx.MockTransport(lambda _: httpx.Response(200, content=fixture))
        async with SourceHTTPClient(transport=transport) as client:
            jobs = await LeverSource("dlocal", name="dLocal Payments", client=client).fetch()

        assert {job.company for job in jobs} == {"dLocal Payments"}
        assert {job.source_name for job in jobs} == {"dLocal Payments"}

    asyncio.run(run())
