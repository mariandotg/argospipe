import asyncio
import sqlite3
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import anthropic

from argospipe.config import (
    AtsSourceConfig,
    Config,
    FileSourceConfig,
    NotionSourceConfig,
    Profile,
    SourceConfig,
    db_path,
)
from argospipe.config import profile_version as read_profile_version
from argospipe.core.extract import extract
from argospipe.core.fingerprint import fingerprint, text_hash
from argospipe.core.models import JobRecord, RunMatch, RunResult, SourceStatus
from argospipe.core.prefilter import prefilter
from argospipe.core.rank import rank
from argospipe.db import connect, migrate
from argospipe.db.repo import (
    add_job_source,
    close_stale_jobs,
    finish_run,
    get_cached_match,
    job_exists,
    open_jobs_without_match,
    record_run_job,
    save_match,
    start_run,
    upsert_job,
)
from argospipe.llm.anthropic import PROMPT_VERSION, AnthropicProvider, LLMOutputError
from argospipe.llm.provider import LLMProvider, Usage, cost_usd
from argospipe.sources.base import RawJob, Source
from argospipe.sources.file_import import FileSource
from argospipe.sources.greenhouse import GreenhouseSource
from argospipe.sources.notion import NotionSource


class _UnavailableSource:
    def __init__(self, name: str, error: str) -> None:
        self.name = name
        self.error = error

    async def fetch(self) -> list[RawJob]:
        raise NotImplementedError(self.error)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def build_source(source: SourceConfig) -> Source:
    if isinstance(source, FileSourceConfig):
        return FileSource(source)
    if isinstance(source, AtsSourceConfig):
        if source.ats == "greenhouse":
            return GreenhouseSource(source.slug, source.name)
        return _UnavailableSource(
            source.name or f"{source.ats}:{source.slug}",
            f"{source.ats} sources are not supported yet",
        )
    if isinstance(source, NotionSourceConfig):
        try:
            return NotionSource(source)
        except NotImplementedError:
            return _UnavailableSource(
                f"notion:{source.database_id}", "Notion sources are not supported yet"
            )
    raise AssertionError(f"Unknown source config: {source!r}")


def _build_sources(configs: Sequence[SourceConfig]) -> list[Source]:
    sources: list[Source] = []
    for source in configs:
        try:
            sources.append(build_source(source))
        except Exception as exc:
            name = getattr(source, "name", None) or source.type
            sources.append(_UnavailableSource(name, str(exc) or type(exc).__name__))
    return sources


async def _fetch(source: Source, timeout_s: float) -> tuple[SourceStatus, list[RawJob]]:
    try:
        jobs = await asyncio.wait_for(source.fetch(), timeout_s)
    except TimeoutError:
        error = f"timed out after {timeout_s:g}s"
        return SourceStatus(name=source.name, ok=False, error=error, fetched=0), []
    except Exception as exc:
        error = str(exc) or type(exc).__name__
        return SourceStatus(name=source.name, ok=False, error=error, fetched=0), []
    row_errors: list[str] = getattr(source, "errors", [])
    status = SourceStatus(
        name=source.name, ok=True, error="; ".join(row_errors) or None, fetched=len(jobs)
    )
    return status, jobs


def _record(raw: RawJob, now: str) -> JobRecord:
    description = None if raw.missing_description else raw.description
    return JobRecord(
        fingerprint=fingerprint(raw.company, raw.title, raw.location),
        company=raw.company.strip(),
        title=raw.title.strip(),
        location=raw.location,
        modality=raw.modality,
        description=description,
        text_hash=text_hash(description) if description else None,
        url=raw.url,
        first_seen=now,
        last_seen=now,
    )


def _ingest(conn: sqlite3.Connection, raw_jobs: list[RawJob], now: str) -> int:
    new: set[str] = set()
    for raw in raw_jobs:
        job = extract(_record(raw, now))
        if job.fingerprint not in new and not job_exists(conn, job.fingerprint):
            new.add(job.fingerprint)
        upsert_job(conn, job, now)
        add_job_source(conn, job.fingerprint, raw.source, raw.external_id, raw.url)
    conn.commit()
    return len(new)


async def _match_all(
    conn: sqlite3.Connection,
    jobs: list[JobRecord],
    config: Config,
    profile: Profile,
    provider: LLMProvider,
    profile_version: str,
    result: RunResult,
    now: str,
) -> None:
    price = config.pricing[config.model]
    semaphore = asyncio.Semaphore(config.match_concurrency)

    def spend(usage: Usage) -> None:
        result.tokens_in += usage.tokens_in
        result.tokens_out += usage.tokens_out
        result.cost_usd += cost_usd(usage, price)

    async def match_one(job: JobRecord) -> RunMatch | None:
        assert job.text_hash is not None
        async with semaphore:
            cached = get_cached_match(
                conn, job.fingerprint, job.text_hash, profile_version, PROMPT_VERSION, config.model
            )
            if cached is not None:
                return RunMatch(job=job, result=cached)
            if result.cost_usd >= config.max_cost_per_run_usd:
                result.cost_cap_reached = True
                return None
            try:
                match, usage = await provider.match(profile.profile, profile.preferences, job)
            except LLMOutputError as exc:
                spend(exc.usage)
                result.failed_count += 1
                return None
            except anthropic.APIError:
                result.failed_count += 1
                return None
            spend(usage)
            save_match(
                conn,
                job.fingerprint,
                job.text_hash,
                profile_version,
                PROMPT_VERSION,
                config.model,
                match,
                usage.tokens_in,
                usage.tokens_out,
                now,
            )
            conn.commit()
            return RunMatch(job=job, result=match)

    # Wait for every task before failing: no match may keep writing after the run closes.
    outcomes = await asyncio.gather(*(match_one(job) for job in jobs), return_exceptions=True)
    errors = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
    result.matches = sorted(
        (outcome for outcome in outcomes if isinstance(outcome, RunMatch)),
        key=lambda m: -m.result.score,
    )
    if errors:
        raise errors[0]
    result.matched_count = len(result.matches)
    for run_match in result.matches:
        record_run_job(conn, result.run_id, run_match.job.fingerprint, "matched", [])
    conn.commit()


async def _execute(
    conn: sqlite3.Connection,
    config: Config,
    profile: Profile,
    result: RunResult,
    *,
    provider: LLMProvider | None,
    sources: Sequence[Source],
    profile_version: str,
    dry_run: bool,
    max_matches: int,
    now: str,
) -> None:
    fetched = await asyncio.gather(*(_fetch(source, config.source_timeout_s) for source in sources))
    result.sources = [status for status, _ in fetched]
    result.new_count = _ingest(conn, [job for _, jobs in fetched for job in jobs], now)

    # A failed source did not refresh last_seen for its jobs: closing now would close live offers.
    if all(status.ok for status in result.sources):
        before = (datetime.fromisoformat(now) - timedelta(days=config.close_after_days)).isoformat(
            timespec="seconds"
        )
        result.closed_count = close_stale_jobs(conn, before)
    conn.commit()

    selected = open_jobs_without_match(conn, profile_version, PROMPT_VERSION, config.model)
    with_description = [job for job in selected if job.description and job.text_hash]
    result.missing_description_count = len(selected) - len(with_description)

    kept, prefiltered_out = prefilter(
        with_description, profile.preferences, profile_stack=profile.profile.stack
    )
    top, ranked_out = rank(kept, profile.profile, max_matches)
    result.discards = prefiltered_out + ranked_out
    result.discarded_count = len(result.discards)
    for discard in result.discards:
        record_run_job(conn, result.run_id, discard.fingerprint, discard.stage, discard.reasons)
    conn.commit()

    if dry_run or not top:
        return
    if provider is None:
        provider = AnthropicProvider(config.model)
    await _match_all(conn, top, config, profile, provider, profile_version, result, now)


def _stats(result: RunResult, dry_run: bool) -> dict[str, object]:
    return {
        "dry_run": dry_run,
        "sources_ok": sum(source.ok for source in result.sources),
        "sources_failed": sum(not source.ok for source in result.sources),
        "new": result.new_count,
        "closed": result.closed_count,
        "missing_description": result.missing_description_count,
        "discarded": dict(Counter(discard.stage for discard in result.discards)),
        "matched": result.matched_count,
        "failed": result.failed_count,
        "cost_cap_reached": result.cost_cap_reached,
    }


async def run(
    config: Config,
    profile: Profile,
    *,
    provider: LLMProvider | None = None,
    sources: Sequence[Source] | None = None,
    conn: sqlite3.Connection | None = None,
    profile_version: str | None = None,
    dry_run: bool = False,
    max_matches: int | None = None,
    now: str | None = None,
) -> RunResult:
    if not dry_run and config.model not in config.pricing:
        raise ValueError(
            f"No price for model {config.model} in config pricing; "
            "the cost cap cannot be enforced without it"
        )
    started_at = now or _utc_now()
    own_conn = conn is None
    db = conn if conn is not None else connect(db_path())
    try:
        migrate(db)
        result = RunResult(run_id=start_run(db, started_at), started_at=started_at)
        db.commit()
        status = "failed"
        try:
            await _execute(
                db,
                config,
                profile,
                result,
                provider=provider,
                sources=sources if sources is not None else _build_sources(config.sources),
                profile_version=profile_version or read_profile_version(),
                dry_run=dry_run,
                max_matches=config.max_matches_per_run if max_matches is None else max_matches,
                now=started_at,
            )
            status = "finished"
        finally:
            result.finished_at = _utc_now()
            finish_run(
                db,
                result.run_id,
                result.finished_at,
                status,
                _stats(result, dry_run),
                result.tokens_in,
                result.tokens_out,
                result.cost_usd,
            )
            db.commit()
        return result
    finally:
        if own_conn:
            db.close()
