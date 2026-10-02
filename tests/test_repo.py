import json
import sqlite3
from pathlib import Path

import pytest

from argospipe.core.models import JobRecord
from argospipe.db import connect, migrate
from argospipe.db.repo import (
    add_job_source,
    close_stale_jobs,
    finish_run,
    get_cached_match,
    open_jobs_without_match,
    record_run_job,
    save_match,
    start_run,
    upsert_job,
)
from argospipe.llm.schemas import MatchResult


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    db = connect(tmp_path / "argospipe.db")
    migrate(db)
    yield db
    db.close()


def _job(fingerprint: str = "job-1", **changes: object) -> JobRecord:
    values: dict[str, object] = {
        "fingerprint": fingerprint,
        "company": "Acme",
        "title": "Engineer",
        "description": "Build APIs",
        "text_hash": "hash-1",
        "stack": ["python", "sqlite"],
        "first_seen": "ignored",
        "last_seen": "ignored",
    }
    values.update(changes)
    return JobRecord.model_validate(values)


def _result() -> MatchResult:
    return MatchResult(score=85, seniority_match="match", summary="Good fit")


def test_upsert_keeps_first_seen_and_updates_mutable_fields(conn: sqlite3.Connection) -> None:
    upsert_job(conn, _job(), "2026-10-01")
    upsert_job(conn, _job(), "2026-10-01")
    upsert_job(
        conn,
        _job(description="Build data services", text_hash="hash-2", stack=["python"]),
        "2026-10-02",
    )

    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    assert row["first_seen"] == "2026-10-01"
    assert row["last_seen"] == "2026-10-02"
    assert row["description"] == "Build data services"
    assert row["text_hash"] == "hash-2"
    assert json.loads(row["stack"]) == ["python"]


def test_source_run_and_stale_job_persistence(conn: sqlite3.Connection) -> None:
    upsert_job(conn, _job(), "2026-10-01")
    add_job_source(conn, "job-1", "csv", "external-1", "https://example.com/1")
    add_job_source(conn, "job-1", "csv", "external-1", "https://example.com/1")
    assert conn.execute("SELECT COUNT(*) FROM job_sources").fetchone()[0] == 1

    run_id = start_run(conn, "2026-10-02")
    record_run_job(conn, run_id, "job-1", "prefiltered_out", ["wrong location"])
    finish_run(conn, run_id, "2026-10-03", "completed", {"discarded": 1}, 10, 2, 0.01)
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "completed"
    assert json.loads(run["stats"]) == {"discarded": 1}
    assert (run["tokens_in"], run["tokens_out"], run["cost_usd"]) == (10, 2, 0.01)
    assert json.loads(conn.execute("SELECT reasons FROM run_jobs").fetchone()[0]) == [
        "wrong location"
    ]
    assert close_stale_jobs(conn, "2026-10-02") == 1
    assert close_stale_jobs(conn, "2026-10-02") == 0


def test_match_cache_requires_every_key_part(conn: sqlite3.Connection) -> None:
    upsert_job(conn, _job(), "2026-10-01")
    upsert_job(conn, _job("job-2"), "2026-10-01")
    key = ("job-1", "hash-1", "profile-1", "prompt-1", "model-1")
    save_match(conn, *key, _result(), 100, 20, "2026-10-01")
    save_match(conn, *key, _result(), 200, 40, "2026-10-02")

    assert get_cached_match(conn, *key) == _result()
    assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1
    for index, changed in enumerate(("job-2", "hash-2", "profile-2", "prompt-2", "model-2")):
        candidate = list(key)
        candidate[index] = changed
        assert get_cached_match(conn, *candidate) is None


def test_open_jobs_without_match_excludes_matched_and_closed(conn: sqlite3.Connection) -> None:
    for fingerprint in ("matched", "new", "closed", "changed"):
        upsert_job(conn, _job(fingerprint), "2026-10-01")
    upsert_job(conn, _job("closed", status="closed"), "2026-10-01")
    save_match(conn, "matched", "hash-1", "profile", "prompt", "model", _result(), 1, 1, "now")
    save_match(conn, "changed", "old-hash", "profile", "prompt", "model", _result(), 1, 1, "now")

    jobs = open_jobs_without_match(conn, "profile", "prompt", "model")
    assert [job.fingerprint for job in jobs] == ["changed", "new"]
    assert jobs[0].stack == ["python", "sqlite"]
    other_jobs = open_jobs_without_match(conn, "other", "prompt", "model")
    assert [job.fingerprint for job in other_jobs] == [
        "changed",
        "matched",
        "new",
    ]
