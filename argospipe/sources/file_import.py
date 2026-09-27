from argospipe.config import FileSourceConfig
from argospipe.sources.base import RawJob


class FileSource:
    name: str
    config: FileSourceConfig

    def __init__(self, config: FileSourceConfig) -> None:
        raise NotImplementedError

    async def fetch(self) -> list[RawJob]:
        raise NotImplementedError
