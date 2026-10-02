from argospipe.config import NotionSourceConfig
from argospipe.core.models import RunResult


async def writeback(result: RunResult, config: NotionSourceConfig) -> None:
    raise NotImplementedError
