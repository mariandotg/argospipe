from argospipe.config import CandidateProfile
from argospipe.core.models import Discard, JobRecord


def rank(
    jobs: list[JobRecord], profile: CandidateProfile, limit: int
) -> tuple[list[JobRecord], list[Discard]]:
    raise NotImplementedError
