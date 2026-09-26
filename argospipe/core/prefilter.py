from argospipe.config import Preferences
from argospipe.core.models import Discard, JobRecord


def prefilter(
    jobs: list[JobRecord], preferences: Preferences
) -> tuple[list[JobRecord], list[Discard]]:
    raise NotImplementedError
