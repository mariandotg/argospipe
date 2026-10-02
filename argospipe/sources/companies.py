from importlib import resources
from typing import Literal

import yaml
from pydantic import BaseModel

from argospipe.config import AtsSourceConfig

Region = Literal["latam", "es", "eu-remote"]


class Company(BaseModel):
    name: str
    ats: Literal["greenhouse", "lever", "ashby"]
    slug: str
    regions: list[Region]


def load_companies() -> list[Company]:
    text = resources.files("argospipe.sources").joinpath("companies.yaml").read_text("utf-8")
    data = yaml.safe_load(text)
    return [Company.model_validate(item) for item in data["companies"]]


def companies_as_sources(regions: list[str] | None = None) -> list[AtsSourceConfig]:
    wanted = set(regions) if regions is not None else None
    return [
        AtsSourceConfig(ats=c.ats, slug=c.slug, name=c.name)
        for c in load_companies()
        if wanted is None or wanted & set(c.regions)
    ]
