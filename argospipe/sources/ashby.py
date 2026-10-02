import asyncio
from typing import Any

from argospipe.config import Modality
from argospipe.sources.base import RawJob
from argospipe.sources.greenhouse import _description
from argospipe.sources.http import SourceHTTPClient


def modality_from_ashby_job(job: dict[str, Any]) -> Modality | None:
    workplace_type = job.get("workplaceType")
    if isinstance(workplace_type, str) and workplace_type.strip():
        normalized = workplace_type.strip().lower().replace("-", "")
        if normalized == "remote":
            return "remote"
        if normalized == "hybrid":
            return "hybrid"
        if normalized == "onsite":
            return "onsite"
        return None  # an unknown workplaceType is not a remote signal
    if job.get("isRemote") is True:
        return "remote"
    return None


def _posted_at(published_at: str | None) -> str | None:
    if not published_at:
        return None
    return published_at[:10]


def _ashby_location(job: dict[str, Any]) -> str:
    parts: list[str] = []
    primary = job.get("location")
    if isinstance(primary, str) and primary.strip():
        parts.append(primary.strip())
    secondary = job.get("secondaryLocations")
    if isinstance(secondary, list):
        for entry in secondary:
            if not isinstance(entry, dict):
                continue
            loc = entry.get("location")
            if isinstance(loc, str) and loc.strip():
                parts.append(loc.strip())
    return "; ".join(parts)


def _ashby_description(job: dict[str, Any]) -> str:
    plain = job.get("descriptionPlain")
    if isinstance(plain, str) and plain.strip():
        return plain
    html = job.get("descriptionHtml")
    if isinstance(html, str) and html.strip():
        return _description(html)
    return ""


class AshbySource:
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
        url = f"https://api.ashbyhq.com/posting-api/job-board/{self.slug}"
        response = await client.get(url)
        data: dict[str, Any] = response.json()
        company = self._company_override or self.slug
        jobs: list[RawJob] = []
        for job in data.get("jobs") or []:
            if not isinstance(job, dict):
                continue
            job_url = job.get("jobUrl") or job.get("applyUrl") or ""
            jobs.append(
                RawJob(
                    title=job["title"],
                    company=company,
                    url=job_url,
                    location=_ashby_location(job) or None,
                    modality=modality_from_ashby_job(job),
                    description=_ashby_description(job),
                    posted_at=_posted_at(job.get("publishedAt")),
                    source_name=company,
                    source=f"ashby:{self.slug}",
                    external_id=str(job["id"]),
                )
            )
        return jobs
