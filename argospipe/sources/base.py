from typing import Protocol

from pydantic import BaseModel, computed_field

from argospipe.config import Modality


class RawJob(BaseModel):
    title: str
    company: str
    url: str
    location: str | None = None
    modality: Modality | None = None
    description: str | None = None
    posted_at: str | None = None
    source_name: str | None = None
    source: str
    external_id: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def missing_description(self) -> bool:
        return self.description is None or not self.description.strip()


class Source(Protocol):
    name: str

    async def fetch(self) -> list[RawJob]: ...
