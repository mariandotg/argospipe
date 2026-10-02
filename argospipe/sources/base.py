from typing import Protocol

from pydantic import computed_field

from argospipe.config import NotionFields


class RawJob(NotionFields):
    location: str | None = None  # type: ignore[assignment]
    description: str | None = None  # type: ignore[assignment]
    posted_at: str | None = None
    source: str
    external_id: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def missing_description(self) -> bool:
        return self.description is None or not self.description.strip()


class Source(Protocol):
    name: str

    async def fetch(self) -> list[RawJob]: ...
