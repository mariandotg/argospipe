import asyncio
from html import unescape
from html.parser import HTMLParser
from typing import Any, ClassVar

from argospipe.sources.base import RawJob
from argospipe.sources.http import SourceHTTPClient


class _TextParser(HTMLParser):
    _blocks: ClassVar[set[str]] = {
        "p",
        "div",
        "li",
        "ul",
        "ol",
        "section",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._blocks or tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._blocks:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _description(content: str) -> str:
    parser = _TextParser()
    parser.feed(unescape(content))
    parser.close()
    return "\n".join(
        " ".join(line.split()) for line in "".join(parser.parts).splitlines() if line.strip()
    )


class GreenhouseSource:
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
        url = f"https://boards-api.greenhouse.io/v1/boards/{self.slug}/jobs?content=true"
        response = await client.get(url)
        data: dict[str, Any] = response.json()
        jobs: list[RawJob] = []
        for job in data["jobs"]:
            company = self._company_override or job["company_name"]
            jobs.append(
                RawJob(
                    title=job["title"],
                    company=company,
                    url=job["absolute_url"],
                    location=job["location"]["name"],
                    description=_description(job["content"]),
                    posted_at=job.get("first_published") or job.get("updated_at"),
                    source_name=company,
                    source=f"greenhouse:{self.slug}",
                    external_id=str(job["id"]),
                )
            )
        return jobs
