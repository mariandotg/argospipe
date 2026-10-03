import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest

from argospipe.config import Modality
from argospipe.sources.ashby import AshbySource, modality_from_ashby_job
from argospipe.sources.http import SourceHTTPClient, SourceNotFoundError

FIXTURES = Path(__file__).parent / "fixtures" / "ats" / "ashby"


def _expected_location(job: dict[str, object]) -> str:
    parts: list[str] = []
    primary = job.get("location")
    if isinstance(primary, str) and primary.strip():
        parts.append(primary.strip())
    secondary_raw = job.get("secondaryLocations")
    secondary = secondary_raw if isinstance(secondary_raw, list) else []
    for entry in secondary:
        if not isinstance(entry, dict):
            continue
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


def test_fallbacks_and_empty_description_are_literal() -> None:
    board = {
        "jobs": [
            {
                "id": "a-1",
                "title": "Platform Engineer",
                "applyUrl": "https://jobs.ashbyhq.com/acme/a-1/application",
                "location": "Madrid",
                "secondaryLocations": [{"location": ""}, {"location": "Barcelona"}],
                "descriptionPlain": "",
                "descriptionHtml": "<p>Build &amp; run <b>Kubernetes</b>.</p>",
                "isRemote": None,
                "workplaceType": None,
                "address": {"postalAddress": {}},
            },
            {
                "id": "a-2",
                "title": "Data Engineer",
                "jobUrl": "https://jobs.ashbyhq.com/acme/a-2",
                "location": "Remote",
                "descriptionPlain": "Exact plain text.",
            },
            {
                "id": "a-3",
                "title": "Empty",
                "jobUrl": "https://jobs.ashbyhq.com/acme/a-3",
                "location": "Remote",
                "descriptionPlain": "",
                "descriptionHtml": "",
            },
        ]
    }
    body = json.dumps(board).encode()

    async def run() -> None:
        transport = httpx.MockTransport(lambda _: httpx.Response(200, content=body))
        async with SourceHTTPClient(transport=transport) as client:
            first, second, third = await AshbySource("acme", client=client).fetch()

        assert first.url == "https://jobs.ashbyhq.com/acme/a-1/application"
        assert first.location == "Madrid; Barcelona"
        assert first.description is not None
        assert "Build & run Kubernetes." in first.description
        assert second.description == "Exact plain text."
        assert third.missing_description is True

    asyncio.run(run())


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        ({"workplaceType": "Remote"}, "remote"),
        ({"workplaceType": "Hybrid"}, "hybrid"),
        ({"workplaceType": "OnSite"}, "onsite"),
        ({"workplaceType": "onsite"}, "onsite"),
        ({"workplaceType": None, "isRemote": True}, "remote"),
        ({"workplaceType": None, "isRemote": False}, None),
        ({"workplaceType": None, "isRemote": None}, None),
        ({"workplaceType": "Flexible"}, None),
    ],
)
def test_modality_from_ashby_fields(job: dict[str, object], expected: Modality | None) -> None:
    assert modality_from_ashby_job(job) == expected


def test_workplace_type_on_fetched_jobs() -> None:
    board = {
        "jobs": [
            {
                "id": "1",
                "title": "Remote",
                "jobUrl": "https://jobs.ashbyhq.com/acme/1",
                "location": "Anywhere",
                "descriptionPlain": "Role",
                "workplaceType": "Remote",
            },
            {
                "id": "2",
                "title": "Hybrid",
                "jobUrl": "https://jobs.ashbyhq.com/acme/2",
                "location": "Madrid",
                "descriptionPlain": "Role",
                "workplaceType": "Hybrid",
            },
            {
                "id": "3",
                "title": "Legacy remote flag",
                "jobUrl": "https://jobs.ashbyhq.com/acme/3",
                "location": "Anywhere",
                "descriptionPlain": "Role",
                "workplaceType": None,
                "isRemote": True,
            },
        ]
    }
    body = json.dumps(board).encode()

    async def run() -> None:
        transport = httpx.MockTransport(lambda _: httpx.Response(200, content=body))
        async with SourceHTTPClient(transport=transport) as client:
            jobs = await AshbySource("acme", client=client).fetch()

        assert [job.modality for job in jobs] == ["remote", "hybrid", "remote"]

    asyncio.run(run())


def test_unknown_workplace_type_is_not_remote_even_if_is_remote() -> None:
    from argospipe.sources.ashby import modality_from_ashby_job

    assert modality_from_ashby_job({"workplaceType": "Flexible", "isRemote": True}) is None
    assert modality_from_ashby_job({"workplaceType": None, "isRemote": True}) == "remote"
