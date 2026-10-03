import json
import sqlite3
from dataclasses import dataclass

from argospipe.core.models import JobRecord
from argospipe.llm.schemas import MatchResult


@dataclass(frozen=True)
class EvalSampleJob:
    fingerprint: str
    title: str
    company: str
    location: str | None
    description: str | None
    score: int | None


def upsert_job(conn: sqlite3.Connection, job: JobRecord, now: str) -> None:
    conn.execute(
        """
        INSERT INTO jobs (
            fingerprint, company, title, location, modality, seniority, stack, lang,
            description, text_hash, url, first_seen, last_seen, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (fingerprint) DO UPDATE SET
            company = excluded.company,
            title = excluded.title,
            location = excluded.location,
            modality = excluded.modality,
            seniority = excluded.seniority,
            stack = excluded.stack,
            lang = excluded.lang,
            description = excluded.description,
            text_hash = excluded.text_hash,
            url = excluded.url,
            last_seen = excluded.last_seen,
            status = excluded.status
        """,
        (
            job.fingerprint,
            job.company,
            job.title,
            job.location,
            job.modality,
            job.seniority,
            json.dumps(job.stack),
            job.lang,
            job.description,
            job.text_hash,
            job.url,
            now,
            now,
            job.status,
        ),
    )


def add_job_source(
    conn: sqlite3.Connection, fingerprint: str, source: str, external_id: str, url: str | None
) -> None:
    conn.execute(
        """
        INSERT INTO job_sources (fingerprint, source, external_id, url)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (source, external_id) DO UPDATE SET
            fingerprint = excluded.fingerprint,
            url = excluded.url
        """,
        (fingerprint, source, external_id, url),
    )


def start_run(conn: sqlite3.Connection, started_at: str) -> int:
    cursor = conn.execute(
        "INSERT INTO runs (started_at, status) VALUES (?, ?)", (started_at, "running")
    )
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    finished_at: str,
    status: str,
    stats: dict[str, object],
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
) -> None:
    conn.execute(
        """
        UPDATE runs SET finished_at = ?, status = ?, stats = ?, tokens_in = ?,
            tokens_out = ?, cost_usd = ?
        WHERE id = ?
        """,
        (finished_at, status, json.dumps(stats), tokens_in, tokens_out, cost_usd, run_id),
    )


def record_run_job(
    conn: sqlite3.Connection, run_id: int, fingerprint: str, stage: str, reasons: list[str]
) -> None:
    conn.execute(
        """
        INSERT INTO run_jobs (run_id, fingerprint, stage, reasons)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (run_id, fingerprint) DO UPDATE SET
            stage = excluded.stage,
            reasons = excluded.reasons
        """,
        (run_id, fingerprint, stage, json.dumps(reasons)),
    )


def get_cached_match(
    conn: sqlite3.Connection,
    fingerprint: str,
    text_hash: str,
    profile_version: str,
    prompt_version: str,
    model: str,
    provider: str,
) -> MatchResult | None:
    row = conn.execute(
        """
        SELECT result FROM matches
        WHERE fingerprint = ? AND text_hash = ? AND profile_version = ?
            AND prompt_version = ? AND model = ? AND provider = ?
        """,
        (fingerprint, text_hash, profile_version, prompt_version, model, provider),
    ).fetchone()
    return MatchResult.model_validate_json(row["result"]) if row is not None else None


def save_match(
    conn: sqlite3.Connection,
    fingerprint: str,
    text_hash: str,
    profile_version: str,
    prompt_version: str,
    model: str,
    provider: str,
    result: MatchResult,
    tokens_in: int,
    tokens_out: int,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO matches (
            fingerprint, text_hash, profile_version, prompt_version, model, provider,
            score, result, tokens_in, tokens_out, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (fingerprint, text_hash, profile_version, prompt_version, model, provider)
            DO NOTHING
        """,
        (
            fingerprint,
            text_hash,
            profile_version,
            prompt_version,
            model,
            provider,
            result.score,
            result.model_dump_json(),
            tokens_in,
            tokens_out,
            created_at,
        ),
    )


def open_jobs_without_match(
    conn: sqlite3.Connection,
    profile_version: str,
    prompt_version: str,
    model: str,
    provider: str,
) -> list[JobRecord]:
    rows = conn.execute(
        """
        SELECT jobs.* FROM jobs
        WHERE jobs.status = 'open'
            AND NOT EXISTS (
                SELECT 1 FROM matches
                WHERE matches.fingerprint = jobs.fingerprint
                    AND matches.text_hash = jobs.text_hash
                    AND matches.profile_version = ?
                    AND matches.prompt_version = ?
                    AND matches.model = ?
                    AND matches.provider = ?
            )
        ORDER BY jobs.fingerprint
        """,
        (profile_version, prompt_version, model, provider),
    ).fetchall()
    return [
        JobRecord.model_validate({**dict(row), "stack": json.loads(row["stack"])}) for row in rows
    ]


def close_stale_jobs(conn: sqlite3.Connection, before: str) -> int:
    cursor = conn.execute(
        "UPDATE jobs SET status = 'closed' WHERE status = 'open' AND last_seen < ?", (before,)
    )
    return cursor.rowcount


def job_exists(conn: sqlite3.Connection, fingerprint: str) -> bool:
    row = conn.execute("SELECT 1 FROM jobs WHERE fingerprint = ?", (fingerprint,)).fetchone()
    return row is not None


def count_jobs(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()
    return int(row["n"])


def _row_to_eval_sample(row: sqlite3.Row, score: int | None) -> EvalSampleJob:
    return EvalSampleJob(
        fingerprint=row["fingerprint"],
        title=row["title"],
        company=row["company"],
        location=row["location"],
        description=row["description"],
        score=score,
    )


def list_latest_matched_jobs(conn: sqlite3.Connection) -> list[EvalSampleJob]:
    rows = conn.execute(
        """
        SELECT fingerprint, score, title, company, location, description
        FROM (
            SELECT
                m.fingerprint,
                m.score,
                j.title,
                j.company,
                j.location,
                j.description,
                ROW_NUMBER() OVER (
                    PARTITION BY m.fingerprint
                    ORDER BY m.created_at DESC, m.rowid DESC
                ) AS rn
            FROM matches m
            JOIN jobs j ON j.fingerprint = m.fingerprint
        )
        WHERE rn = 1
        ORDER BY fingerprint
        """
    ).fetchall()
    return [_row_to_eval_sample(row, int(row["score"])) for row in rows]


def list_prefiltered_jobs(conn: sqlite3.Connection, exclude: set[str]) -> list[EvalSampleJob]:
    if exclude:
        placeholders = ",".join("?" for _ in exclude)
        query = f"""
            SELECT DISTINCT j.fingerprint, j.title, j.company, j.location, j.description
            FROM run_jobs rj
            JOIN jobs j ON j.fingerprint = rj.fingerprint
            WHERE rj.stage = 'prefiltered_out'
                AND TRIM(COALESCE(j.description, '')) != ''
                AND j.fingerprint NOT IN ({placeholders})
            ORDER BY j.fingerprint
        """
        rows = conn.execute(query, tuple(exclude)).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT DISTINCT j.fingerprint, j.title, j.company, j.location, j.description
            FROM run_jobs rj
            JOIN jobs j ON j.fingerprint = rj.fingerprint
            WHERE rj.stage = 'prefiltered_out'
                AND TRIM(COALESCE(j.description, '')) != ''
            ORDER BY j.fingerprint
            """
        ).fetchall()
    return [_row_to_eval_sample(row, None) for row in rows]
