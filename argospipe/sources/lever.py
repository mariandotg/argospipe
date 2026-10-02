import asyncio
from datetime import UTC, datetime
from typing import Any

from argospipe.config import Modality
from argospipe.sources.base import RawJob
from argospipe.sources.greenhouse import _description
from argospipe.sources.http import SourceHTTPClient


def _posted_at(created_at_ms: int) -> str:
    return datetime.fromtimestamp(created_at_ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def modality_from_workplace_type(workplace_type: object) -> Modality | None:
    if workplace_type == "remote":
        return "remote"
    if workplace_type == "hybrid":
        return "hybrid"
    if workplace_type == "onsite":
        return "onsite"
    return None


def _location(categories: dict[str, Any]) -> str | None:
    location = categories.get("location")
    if location:
        return str(location)
    all_locations = categories.get("allLocations") or []
    if all_locations:
        return ", ".join(str(loc) for loc in all_locations)
    return None


def _posting_description(posting: dict[str, Any]) -> str | None:
    parts: list[str] = []

    description_plain = (posting.get("descriptionPlain") or "").strip()
    if description_plain:
        parts.append(description_plain)

    for item in posting.get("lists") or []:
        heading = (item.get("text") or "").strip()
        content_html = item.get("content") or ""
        content = _description(content_html) if content_html.strip() else ""
        section_parts: list[str] = []
        if heading:
            section_parts.append(heading)
        if content:
            section_parts.append(content)
        if section_parts:
            parts.append("\n".join(section_parts))

    additional = (posting.get("additionalPlain") or "").strip()
    if additional:
        parts.append(additional)

    if not parts:
        return None
    return "\n\n".join(parts)


class LeverSource:
    def __init__(
        self, slug: str, name: str | None = None, client: SourceHTTPClient | None = None
    ) -> None:
        self.slug = slug
        self.name = name or slug
        self._company_override = name
        self._client = client
        self._semaphore = asyncio.Semaphore(2)

    async def fetch(self) -> list[RawJob]:
        if self._client is None:
            async with SourceHTTPClient(semaphore=self._semaphore) as client:
                return await self._fetch(client)
        return await self._fetch(self._client)

    async def _fetch(self, client: SourceHTTPClient) -> list[RawJob]:
        url = f"https://api.lever.co/v0/postings/{self.slug}?mode=json"
        response = await client.get(url)
        postings: list[dict[str, Any]] = response.json()
        jobs: list[RawJob] = []
        company = self._company_override or self.slug
        for posting in postings:
            categories = posting.get("categories") or {}
            jobs.append(
                RawJob(
                    title=posting["text"],
                    company=company,
                    url=posting["hostedUrl"],
                    location=_location(categories),
                    modality=modality_from_workplace_type(posting.get("workplaceType")),
                    description=_posting_description(posting),
                    posted_at=_posted_at(posting["createdAt"]),
                    source_name=company,
                    source=f"lever:{self.slug}",
                    external_id=posting["id"],
                )
            )
        return jobs
