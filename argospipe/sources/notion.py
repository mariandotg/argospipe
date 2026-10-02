from argospipe.config import NotionSourceConfig
from argospipe.sources.base import RawJob


class NotionSource:
    name: str
    config: NotionSourceConfig

    def __init__(self, config: NotionSourceConfig) -> None:
        raise NotImplementedError

    async def fetch(self) -> list[RawJob]:
        raise NotImplementedError
