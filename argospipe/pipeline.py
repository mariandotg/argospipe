from argospipe.config import Config, Profile
from argospipe.core.models import RunResult


async def run(config: Config, profile: Profile) -> RunResult:
    raise NotImplementedError
