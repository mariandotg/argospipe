import asyncio
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import anthropic
import httpx
import pytest

from argospipe import config as config_module
from argospipe import pipeline
from argospipe.config import (
    AtsSourceConfig,
    CandidateProfile,
    Config,
    FileSourceConfig,
    Preferences,
    Profile,
)
from argospipe.core.extract import extract
from argospipe.core.fingerprint import fingerprint
from argospipe.core.models import JobRecord, RunResult
from argospipe.db import connect, migrate
from argospipe.llm.anthropic import LLMOutputError
from argospipe.llm.provider import Usage
from argospipe.llm.schemas import MatchResult, ProfileExtraction
from argospipe.sources.base import RawJob, Source

DESCRIPTION = "We build backend services in Python and PostgreSQL. Fully remote team."
PROFILE = Profile(
    profile=CandidateProfile(
        roles=["Backend Engineer"], seniority="senior", stack=["Python", "PostgreSQL"]
    ),
    preferences=Preferences(min_seniority="senior", excluded_companies=["Evil Corp"]),
)


def raw(
    title: str, company: str, description: str | None = DESCRIPTION, external_id: str = ""
) -> RawJob:
    return RawJob(
        title=title,
        company=company,
        url=f"https://example.com/{company}/{title}",
        location="Remote",
        description=description,
        source="fake",
        external_id=external_id or f"{company}-{title}",
    )


JOBS = [
    raw("Senior Backend Engineer", "Acme"),
    raw("Senior Python Engineer", "Globex"),
    raw("Senior Backend Engineer", "Evil Corp"),
    raw("Junior Backend Engineer", "Initech"),
    raw("Senior Backend Engineer", "Hooli", description="   "),
]


class FakeSource:
    def __init__(self, jobs: list[RawJob], name: str = "fake") -> None:
        self.name = name
        self.jobs = jobs

    async def fetch(self) -> list[RawJob]:
        return self.jobs


class FailingSource:
    name = "broken"

    async def fetch(self) -> list[RawJob]:
        raise RuntimeError("board is down")


class FakeProvider:
    def __init__(self, usage: Usage | None = None, fail_for: str | None = None) -> None:
        self.usage = usage or Usage(tokens_in=1000, tokens_out=100)
        self.fail_for = fail_for
        self.calls: list[JobRecord] = []

    async def extract_profile(self, cv_text: str) -> tuple[ProfileExtraction, Usage]:
        raise AssertionError("not used")

    async def match(
        self, profile: CandidateProfile, preferences: Preferences, job: JobRecord
    ) -> tuple[MatchResult, Usage]:
        self.calls.append(job)
        if job.company == self.fail_for:
            raise LLMOutputError(self.usage)
        result = MatchResult(score=80, seniority_match="match", summary=f"{job.company} fits.")
        return result, self.usage


@pytest.fixture
def conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[sqlite3.Connection]:
    monkeypatch.setenv(config_module.HOME_ENV, str(tmp_path))
    connection = connect(config_module.db_path())
    migrate(connection)
    yield connection
    connection.close()


def run(
    conn: sqlite3.Connection,
    provider: FakeProvider | None,
    sources: list[Source],
    config: Config | None = None,
    *,
    dry_run: bool = False,
    max_matches: int | None = None,
) -> RunResult:
    return asyncio.run(
        pipeline.run(
            config or Config(),
            PROFILE,
            provider=provider,
            sources=sources,
            conn=conn,
            profile_version="pv1",
            dry_run=dry_run,
            max_matches=max_matches,
            now="2026-10-01T10:00:00+00:00",
        )
    )


def test_run_matches_discards_and_reports_stats(conn: sqlite3.Connection) -> None:
    provider = FakeProvider()

    result = run(conn, provider, [FakeSource(JOBS)], max_matches=1)

    assert result.sources[0].ok and result.sources[0].fetched == 5
    assert result.new_count == 5
    assert result.missing_description_count == 1
    assert len(provider.calls) == 1
    assert result.matched_count == 1
    assert result.matches[0].result.score == 80
    stages = {discard.stage for discard in result.discards}
    assert stages == {"prefiltered_out", "ranked_out"}
    assert result.discarded_count == 3
    reasons = [reason for discard in result.discards for reason in discard.reasons]
    assert any("Evil Corp is excluded" in reason for reason in reasons)
    assert any("below minimum senior" in reason for reason in reasons)
    assert (result.tokens_in, result.tokens_out) == (1000, 100)
    assert result.cost_usd == pytest.approx(0.0015)

    run_jobs = conn.execute("SELECT stage, COUNT(*) FROM run_jobs GROUP BY stage").fetchall()
    assert dict(map(tuple, run_jobs)) == {"matched": 1, "prefiltered_out": 2, "ranked_out": 1}
    row = conn.execute("SELECT status, stats, cost_usd FROM runs").fetchone()
    assert row["status"] == "finished"
    assert row["cost_usd"] == pytest.approx(0.0015)
    stats = json.loads(row["stats"])
    assert stats["matched"] == 1
    assert stats["discarded"] == {"prefiltered_out": 2, "ranked_out": 1}
    assert stats["missing_description"] == 1


def test_second_run_makes_no_provider_calls(conn: sqlite3.Connection) -> None:
    first = FakeProvider()
    run(conn, first, [FakeSource(JOBS)])
    assert len(first.calls) == 2

    second = FakeProvider()
    result = run(conn, second, [FakeSource(JOBS)])

    assert second.calls == []
    assert result.new_count == 0
    assert result.matched_count == 0
    assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 2


def test_cost_cap_stops_matching(conn: sqlite3.Connection) -> None:
    jobs = [raw(f"Senior Backend Engineer {n}", f"Company {n}") for n in range(4)]
    provider = FakeProvider()
    config = Config(max_cost_per_run_usd=0.002, match_concurrency=1)

    result = run(conn, provider, [FakeSource(jobs)], config)

    assert len(provider.calls) == 2
    assert result.matched_count == 2
    assert result.cost_cap_reached is True
    stats = json.loads(conn.execute("SELECT stats FROM runs").fetchone()["stats"])
    assert stats["cost_cap_reached"] is True


def test_failing_source_does_not_stop_the_run(conn: sqlite3.Connection) -> None:
    result = run(conn, FakeProvider(), [FailingSource(), FakeSource(JOBS)])

    broken, fake = result.sources
    assert (broken.ok, broken.error, broken.fetched) == (False, "board is down", 0)
    assert fake.ok
    assert result.matched_count == 2


def test_unsupported_configured_sources_fail_without_crashing(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(json.dumps([JOBS[0].model_dump(exclude={"missing_description"})]))
    config = Config(
        sources=[
            AtsSourceConfig(ats="lever", slug="acme"),
            FileSourceConfig(path=jobs_file),
        ]
    )

    result = asyncio.run(
        pipeline.run(config, PROFILE, provider=FakeProvider(), conn=conn, profile_version="pv1")
    )

    lever, file = result.sources
    assert lever.ok is False
    assert lever.error == "lever sources are not supported yet"
    assert (file.ok, file.fetched) == (True, 1)
    assert result.matched_count == 1


def test_missing_description_never_reaches_provider(conn: sqlite3.Connection) -> None:
    provider = FakeProvider()
    jobs = [raw("Senior Backend Engineer", "Acme", description=None), JOBS[1]]

    result = run(conn, provider, [FakeSource(jobs)])

    assert [job.company for job in provider.calls] == ["Globex"]
    assert result.missing_description_count == 1


def test_dry_run_makes_no_provider_calls(conn: sqlite3.Connection) -> None:
    provider = FakeProvider()

    result = run(conn, provider, [FakeSource(JOBS)], dry_run=True)

    assert provider.calls == []
    assert result.matches == []
    assert result.cost_usd == 0
    assert result.new_count == 5
    assert result.discarded_count == 2


def test_failed_match_counts_cost_and_run_continues(conn: sqlite3.Connection) -> None:
    provider = FakeProvider(fail_for="Acme")

    result = run(conn, provider, [FakeSource(JOBS)])

    assert len(provider.calls) == 2
    assert result.failed_count == 1
    assert [match.job.company for match in result.matches] == ["Globex"]
    assert result.cost_usd == pytest.approx(0.003)


class ApiErrorProvider(FakeProvider):
    async def match(
        self, profile: CandidateProfile, preferences: Preferences, job: JobRecord
    ) -> tuple[MatchResult, Usage]:
        if job.company == "Acme":
            request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
            raise anthropic.APIError("service unavailable", request, body=None)
        return await super().match(profile, preferences, job)


def test_api_error_records_unscored_failed(conn: sqlite3.Connection) -> None:
    result = run(conn, ApiErrorProvider(), [FakeSource(JOBS)])

    assert result.failed_count == 1
    failed = [u for u in result.unscored if u.kind == "failed"]
    assert len(failed) == 1
    acme_fp = fingerprint("Acme", "Senior Backend Engineer", "Remote")
    assert failed[0].fingerprint == acme_fp
    assert "service unavailable" in failed[0].reasons[0]
    assert [match.job.company for match in result.matches] == ["Globex"]


def test_run_without_price_for_model_fails_before_matching(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="No price for model"):
        run(conn, FakeProvider(), [FakeSource(JOBS)], Config(model="unknown-model"))


def test_prefilter_uses_profile_stack(conn: sqlite3.Connection) -> None:
    provider = FakeProvider()
    java = raw(
        "Senior Backend Engineer", "Umbrella", description="We use Java, Kotlin and Spring Boot."
    )

    result = run(conn, provider, [FakeSource([java])])

    assert provider.calls == []
    assert result.discards[0].stage == "prefiltered_out"
    assert "shares nothing with the profile" in result.discards[0].reasons[0]


class CrashingProvider(FakeProvider):
    async def match(
        self, profile: CandidateProfile, preferences: Preferences, job: JobRecord
    ) -> tuple[MatchResult, Usage]:
        if job.company == "Acme":
            raise RuntimeError("provider bug")
        await asyncio.sleep(0.05)
        return await super().match(profile, preferences, job)


def test_unexpected_error_waits_for_other_matches_then_fails(conn: sqlite3.Connection) -> None:
    with pytest.raises(RuntimeError, match="provider bug"):
        run(conn, CrashingProvider(), [FakeSource(JOBS)])

    saved = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    assert saved == 1


class RaisingFetchSource:
    name = "raising"

    async def fetch(self) -> list[RawJob]:
        raise ValueError("bad payload")


class SlowSource:
    name = "slow"

    async def fetch(self) -> list[RawJob]:
        await asyncio.sleep(5)
        return []


def test_stale_job_is_closed_and_not_matched(conn: sqlite3.Connection) -> None:
    run(conn, FakeProvider(), [FakeSource(JOBS[:1])])
    conn.execute("UPDATE jobs SET last_seen = '2026-09-01T10:00:00+00:00'")
    conn.execute("DELETE FROM matches")
    provider = FakeProvider()

    result = run(conn, provider, [FakeSource([])])

    assert result.closed_count == 1
    assert provider.calls == []
    assert conn.execute("SELECT status FROM jobs").fetchone()["status"] == "closed"
    assert (
        json.loads(conn.execute("SELECT stats FROM runs ORDER BY id DESC").fetchone()["stats"])[
            "closed"
        ]
        == 1
    )


def test_dry_run_closes_stale_jobs(conn: sqlite3.Connection) -> None:
    run(conn, FakeProvider(), [FakeSource(JOBS[:1])])
    conn.execute("UPDATE jobs SET last_seen = '2026-09-01T10:00:00+00:00'")

    result = run(conn, None, [FakeSource([])], dry_run=True)

    assert result.closed_count == 1


def test_closed_job_seen_again_is_reopened(conn: sqlite3.Connection) -> None:
    run(conn, FakeProvider(), [FakeSource(JOBS[:1])])
    conn.execute("UPDATE jobs SET status = 'closed', last_seen = '2026-09-01T10:00:00+00:00'")

    run(conn, FakeProvider(), [FakeSource(JOBS[:1])])

    row = conn.execute("SELECT status, last_seen FROM jobs").fetchone()
    assert (row["status"], row["last_seen"]) == ("open", "2026-10-01T10:00:00+00:00")


def test_source_failing_at_construction_is_isolated(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def boom(source: object) -> Source:
        raise RuntimeError("cannot build")

    monkeypatch.setattr(pipeline, "build_source", boom)
    config = Config(sources=[FileSourceConfig(path=tmp_path / "jobs.json")])

    result = asyncio.run(
        pipeline.run(config, PROFILE, provider=FakeProvider(), conn=conn, profile_version="pv1")
    )

    assert [(s.ok, s.error) for s in result.sources] == [(False, "cannot build")]


def test_source_raising_in_fetch_is_isolated(conn: sqlite3.Connection) -> None:
    result = run(conn, FakeProvider(), [RaisingFetchSource(), FakeSource(JOBS)])

    assert [s.ok for s in result.sources] == [False, True]
    assert result.sources[0].error == "bad payload"


def test_source_timeout_is_isolated(conn: sqlite3.Connection) -> None:
    result = run(
        conn, FakeProvider(), [SlowSource(), FakeSource(JOBS)], Config(source_timeout_s=0.01)
    )

    slow, fake = result.sources
    assert (slow.ok, slow.error) == (False, "timed out after 0.01s")
    assert fake.ok
    assert result.matched_count == 2


def test_failed_source_blocks_closing_live_offers(conn: sqlite3.Connection) -> None:
    run(conn, FakeProvider(), [FakeSource(JOBS[:1])])
    conn.execute("UPDATE jobs SET last_seen = '2026-09-01T10:00:00+00:00'")

    result = run(conn, None, [FakeSource([]), FailingSource()], dry_run=True)

    assert result.closed_count == 0
    assert conn.execute("SELECT status FROM jobs").fetchone()["status"] == "open"


def test_source_timeout_must_be_positive() -> None:
    with pytest.raises(ValueError):
        Config(source_timeout_s=0)


def test_run_fills_links_for_ingested_jobs(conn: sqlite3.Connection) -> None:
    result = run(conn, FakeProvider(), [FakeSource(JOBS)])

    assert len(result.links) == 5
    for link, job in zip(result.links, JOBS, strict=True):
        assert link.source == job.source
        assert link.external_id == job.external_id
        assert link.fingerprint == fingerprint(job.company, job.title, job.location)


def test_run_fills_unscored_for_missing_description_and_failed_match(
    conn: sqlite3.Connection,
) -> None:
    provider = FakeProvider(fail_for="Acme")

    result = run(conn, provider, [FakeSource(JOBS)])

    missing = [u for u in result.unscored if u.kind == "missing_description"]
    assert len(missing) == 1
    hooli_fp = fingerprint("Hooli", "Senior Backend Engineer", "Remote")
    assert missing[0].fingerprint == hooli_fp

    failed = [u for u in result.unscored if u.kind == "failed"]
    assert len(failed) == 1
    acme_fp = fingerprint("Acme", "Senior Backend Engineer", "Remote")
    assert failed[0].fingerprint == acme_fp
    assert failed[0].reasons


def test_structured_modality_survives_extract(conn: sqlite3.Connection) -> None:
    job = raw(
        "Senior Backend Engineer",
        "Acme",
        description="Collaboration hubs only; no remote/hybrid wording.",
    )
    job.modality = "hybrid"

    run(conn, FakeProvider(), [FakeSource([job])])

    row = conn.execute("SELECT modality FROM jobs").fetchone()
    assert row["modality"] == "hybrid"


def test_record_preserves_modality_before_extract() -> None:
    raw_job = RawJob(
        title="Engineer",
        company="Co",
        url="https://example.com/1",
        modality="remote",
        description="",
        source="lever:acme",
        external_id="1",
    )
    record = pipeline._record(raw_job, "2026-10-01")
    assert record.modality == "remote"
    assert extract(record).modality == "remote"
