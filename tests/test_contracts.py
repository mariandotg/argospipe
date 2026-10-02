import pytest
from pydantic import ValidationError

from argospipe.core.models import Discard, JobRecord, RunMatch, RunResult, SourceStatus
from argospipe.llm.schemas import FitReason, MatchResult, ProfileExtraction
from argospipe.sources.base import RawJob


def test_raw_job_validates_and_marks_missing_description() -> None:
    job = RawJob(
        title="Backend Engineer",
        company="Acme",
        url="https://example.com/job",
        source="file",
        external_id="job-1",
    )

    assert job.location is None
    assert job.description is None
    assert job.missing_description is True
    assert job.model_dump()["missing_description"] is True


@pytest.mark.parametrize("description", ["", "   ", "\n\t"])
def test_raw_job_marks_blank_description_as_missing(description: str) -> None:
    job = RawJob(
        title="Backend Engineer",
        company="Acme",
        url="https://example.com/job",
        description=description,
        source="file",
        external_id="job-1",
    )

    assert job.missing_description is True


def test_raw_job_accepts_optional_notion_fields() -> None:
    job = RawJob(
        title="Backend Engineer",
        company="Acme",
        location="Remote",
        url="https://example.com/job",
        description="Build services.",
        posted_at="2026-09-30",
        source_name="Careers page",
        source="notion",
        external_id="page-1",
    )

    assert job.missing_description is False
    assert job.source_name == "Careers page"


def test_raw_job_requires_shared_notion_fields() -> None:
    with pytest.raises(ValidationError):
        RawJob(source="file", external_id="job-1")


@pytest.mark.parametrize("score", [0, 100])
def test_match_result_accepts_score_bounds(score: int) -> None:
    result = MatchResult(score=score, seniority_match="match", summary="Strong fit.")

    assert result.score == score


@pytest.mark.parametrize("score", [-1, 101])
def test_match_result_rejects_scores_outside_bounds(score: int) -> None:
    with pytest.raises(ValidationError):
        MatchResult(score=score, seniority_match="match", summary="Strong fit.")


def test_match_result_rejects_invalid_seniority_match() -> None:
    with pytest.raises(ValidationError):
        MatchResult(score=80, seniority_match="equal", summary="Strong fit.")  # type: ignore[arg-type]


def test_profile_extraction_validates_spec_shape() -> None:
    profile = ProfileExtraction.model_validate(
        {
            "roles": ["Backend Engineer"],
            "seniority": "senior",
            "years_experience": 8,
            "stack": ["python", "postgresql"],
            "languages": ["Spanish", "English"],
            "highlights": ["Reduced processing time by 40%"],
        }
    )

    assert profile.roles == ["Backend Engineer"]
    assert profile.seniority == "senior"
    assert profile.years_experience == 8
    assert profile.stack == ["python", "postgresql"]
    assert profile.languages == ["Spanish", "English"]
    assert profile.highlights == ["Reduced processing time by 40%"]


def test_profile_extraction_rejects_unknown_seniority() -> None:
    with pytest.raises(ValidationError):
        ProfileExtraction(seniority="expert")  # type: ignore[arg-type]


def test_run_result_round_trips_to_json() -> None:
    job = JobRecord(
        fingerprint="abc123",
        company="Acme",
        title="Backend Engineer",
        location="Remote",
        modality="remote",
        seniority="senior",
        stack=["python"],
        lang="en",
        description="Build services.",
        text_hash="def456",
        url="https://example.com/job",
        first_seen="2026-09-30T10:00:00Z",
        last_seen="2026-09-30T10:00:00Z",
    )
    result = RunResult(
        run_id=7,
        started_at="2026-09-30T10:00:00Z",
        finished_at="2026-09-30T10:01:00Z",
        sources=[SourceStatus(name="notion", ok=True, fetched=2)],
        new_count=1,
        discarded_count=1,
        missing_description_count=0,
        matched_count=1,
        matches=[
            RunMatch(
                job=job,
                result=MatchResult(
                    score=90,
                    fit_reasons=[
                        FitReason(reason="Relevant backend work", evidence="Built Python APIs")
                    ],
                    gaps=["No Kubernetes experience"],
                    red_flags=[],
                    seniority_match="match",
                    summary="Strong fit.",
                ),
            )
        ],
        discards=[
            Discard(
                fingerprint="ghi789",
                stage="prefiltered_out",
                reasons=["Company is excluded"],
            )
        ],
        tokens_in=1200,
        tokens_out=150,
        cost_usd=0.00195,
    )

    assert RunResult.model_validate_json(result.model_dump_json()) == result
