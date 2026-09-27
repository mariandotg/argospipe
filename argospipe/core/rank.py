from argospipe.config import CandidateProfile
from argospipe.core.models import Discard, JobRecord
from argospipe.core.normalize import normalize_stack, normalize_title

SENIORITY_ORDER = ("intern", "junior", "semi-senior", "senior", "lead", "principal")


def _score(job: JobRecord, profile: CandidateProfile) -> float:
    job_stack = normalize_stack(job.stack)
    profile_stack = normalize_stack(profile.stack)
    stack_overlap = (
        len(job_stack & profile_stack) / len(job_stack | profile_stack)
        if job_stack and profile_stack
        else 0.0
    )

    title_tokens = set(normalize_title(job.title).split())
    role_tokens = {token for role in profile.roles for token in normalize_title(role).split()}
    role_overlap = (
        len(title_tokens & role_tokens) / len(title_tokens | role_tokens)
        if title_tokens and role_tokens
        else 0.0
    )

    seniority_closeness = (
        1
        - abs(SENIORITY_ORDER.index(job.seniority) - SENIORITY_ORDER.index(profile.seniority))
        / (len(SENIORITY_ORDER) - 1)
        if job.seniority is not None and profile.seniority is not None
        else 0.0
    )
    return 0.5 * stack_overlap + 0.3 * role_overlap + 0.2 * seniority_closeness


def rank(
    jobs: list[JobRecord], profile: CandidateProfile, limit: int
) -> tuple[list[JobRecord], list[Discard]]:
    scored = sorted(((job, _score(job, profile)) for job in jobs), key=lambda item: -item[1])
    cutoff = max(limit, 0)
    top = [job for job, _ in scored[:cutoff]]
    ranked_out = [
        Discard(
            fingerprint=job.fingerprint,
            stage="ranked_out",
            reasons=[f"score {score:.3f} below top {limit}"],
        )
        for job, score in scored[cutoff:]
    ]
    return top, ranked_out
