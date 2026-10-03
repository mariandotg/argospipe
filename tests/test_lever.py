import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from argospipe.config import Modality
from argospipe.sources.http import SourceHTTPClient, SourceNotFoundError
from argospipe.sources.lever import LeverSource, modality_from_workplace_type

FIXTURES = Path(__file__).parent / "fixtures" / "ats" / "lever"


def _posted_at(created_at_ms: int) -> str:
    return datetime.fromtimestamp(created_at_ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def _expected_location(categories: dict[str, object]) -> str | None:
    location = categories.get("location")
    if location:
        return str(location)
    all_locations_raw = categories.get("allLocations")
    all_locations: list[object] = all_locations_raw if isinstance(all_locations_raw, list) else []
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


def test_description_order_and_location_fallback_are_literal() -> None:
    posting = {
        "id": "p-1",
        "text": "Backend Engineer",
        "hostedUrl": "https://jobs.lever.co/acme/p-1",
        "createdAt": 1759276800000,
        "categories": {"allLocations": ["Buenos Aires", "Remote - LATAM"]},
        "descriptionPlain": "About the role.",
        "lists": [
            {"text": "Requirements", "content": "<li>Java</li><li>Spring</li>"},
            {"text": "Nice to have", "content": "<li>Kotlin</li>"},
        ],
        "additionalPlain": "We offer remote work.",
    }
    body = json.dumps([posting]).encode()

    async def run() -> None:
        transport = httpx.MockTransport(lambda _: httpx.Response(200, content=body))
        async with SourceHTTPClient(transport=transport) as client:
            [job] = await LeverSource("acme", client=client).fetch()

        assert job.location == "Buenos Aires, Remote - LATAM"
        assert job.posted_at == "2025-10-01"
        assert job.description is not None
        sections = [s.strip() for s in job.description.split("\n\n")]
        assert sections[0] == "About the role."
        assert sections[1].splitlines()[0] == "Requirements"
        assert "Java" in sections[1] and "Spring" in sections[1]
        assert sections[2].splitlines()[0] == "Nice to have"
        assert "Kotlin" in sections[2]
        assert sections[-1] == "We offer remote work."

    asyncio.run(run())


@pytest.mark.parametrize(
    ("workplace_type", "expected"),
    [
        ("remote", "remote"),
        ("hybrid", "hybrid"),
        ("onsite", "onsite"),
        ("REMOTE", None),
        ("on-site", None),
        ("", None),
        (None, None),
    ],
)
def test_workplace_type_maps_modality(workplace_type: object, expected: Modality | None) -> None:
    assert modality_from_workplace_type(workplace_type) == expected


def test_workplace_type_on_fetched_jobs() -> None:
    board = [
        {
            "id": "1",
            "text": "Remote role",
            "hostedUrl": "https://jobs.lever.co/acme/1",
            "createdAt": 1759276800000,
            "categories": {},
            "workplaceType": "remote",
        },
        {
            "id": "2",
            "text": "Hybrid role",
            "hostedUrl": "https://jobs.lever.co/acme/2",
            "createdAt": 1759276800000,
            "categories": {},
            "workplaceType": "hybrid",
        },
        {
            "id": "3",
            "text": "Unknown",
            "hostedUrl": "https://jobs.lever.co/acme/3",
            "createdAt": 1759276800000,
            "categories": {},
            "workplaceType": "flexible",
        },
    ]
    body = json.dumps(board).encode()

    async def run() -> None:
        transport = httpx.MockTransport(lambda _: httpx.Response(200, content=body))
        async with SourceHTTPClient(transport=transport) as client:
            jobs = await LeverSource("acme", client=client).fetch()

        assert [job.modality for job in jobs] == ["remote", "hybrid", None]

    asyncio.run(run())
