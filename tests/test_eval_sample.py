import sqlite3
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from argospipe.cli import app
from argospipe.config import Preferences, Profile, save_profile
from argospipe.core.models import JobRecord
from argospipe.db import connect, migrate
from argospipe.db.repo import record_run_job, save_match, start_run, upsert_job
from argospipe.eval import load_pairs
from argospipe.eval_sample import (
    BUCKET_MARGIN,
    classify_match_score,
    sample_eval_jobs,
    write_sample_pairs,
)
from argospipe.llm.schemas import MatchResult

runner = CliRunner()


def _job(fingerprint: str, title: str = "Engineer") -> JobRecord:
    return JobRecord.model_validate(
        {
            "fingerprint": fingerprint,
            "company": "Acme",
            "title": title,
            "description": f"Role {fingerprint}",
            "text_hash": f"hash-{fingerprint}",
            "stack": [],
            "first_seen": "2026-01-01",
            "last_seen": "2026-01-01",
        }
    )


def _match(score: int) -> MatchResult:
    return MatchResult(score=score, seniority_match="match", summary="ok")


def _seed_match(
    conn: sqlite3.Connection,
    fingerprint: str,
    score: int,
    *,
    created_at: str = "2026-01-01",
) -> None:
    upsert_job(conn, _job(fingerprint), "2026-01-01")
    save_match(
        conn,
        fingerprint,
        f"hash-{fingerprint}",
        "profile",
        "prompt",
        "model",
        "anthropic",
        _match(score),
        1,
        1,
        created_at,
    )


@pytest.fixture
def data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ARGOSPIPE_HOME", str(tmp_path))
    db = connect(tmp_path / "argospipe.db")
    migrate(db)
    db.close()
    return tmp_path


def test_classify_match_score_uses_threshold() -> None:
    threshold = 70
    assert classify_match_score(threshold + BUCKET_MARGIN, threshold) == "high"
    assert classify_match_score(threshold + BUCKET_MARGIN - 1, threshold) == "borderline"
    assert classify_match_score(threshold - BUCKET_MARGIN, threshold) == "borderline"
    assert classify_match_score(threshold - BUCKET_MARGIN - 1, threshold) == "low"


def test_sample_stratifies_matched_jobs(data_home: Path) -> None:
    threshold = 70
    conn = connect(data_home / "argospipe.db")
    for index, score in enumerate([95, 96, 97, 98, 99, 10, 11, 12, 13, 14, 70, 71, 72, 73, 74]):
        _seed_match(conn, f"job-{index}", score)
    upsert_job(conn, _job("pre-1"), "2026-01-02")
    upsert_job(conn, _job("pre-2"), "2026-01-02")
    run_id = start_run(conn, "2026-01-02")
    record_run_job(conn, run_id, "pre-1", "prefiltered_out", ["lang"])
    record_run_job(conn, run_id, "pre-2", "prefiltered_out", ["stack"])

    sampled = sample_eval_jobs(conn, n=12, threshold=threshold, seed=42)
    conn.commit()
    conn.close()

    by_bucket: dict[str, int] = {"high": 0, "borderline": 0, "low": 0, "prefiltered": 0}
    for job in sampled:
        if job.score is None:
            by_bucket["prefiltered"] += 1
        else:
            by_bucket[classify_match_score(job.score, threshold)] += 1

    assert len(sampled) == 12
    assert len({job.fingerprint for job in sampled}) == 12
    assert by_bucket["prefiltered"] <= int(12 * 0.2)
    assert by_bucket["high"] >= 2
    assert by_bucket["borderline"] >= 2
    assert by_bucket["low"] >= 2


def test_sample_seed_is_deterministic(data_home: Path) -> None:
    conn = connect(data_home / "argospipe.db")
    for index in range(20):
        _seed_match(conn, f"job-{index}", 50 + index)
    first = sample_eval_jobs(conn, n=10, threshold=70, seed=7)
    second = sample_eval_jobs(conn, n=10, threshold=70, seed=7)
    conn.close()
    assert [job.fingerprint for job in first] == [job.fingerprint for job in second]


def test_write_sample_pairs_omits_model_score(data_home: Path) -> None:
    conn = connect(data_home / "argospipe.db")
    _seed_match(conn, "job-a", 88)
    jobs = sample_eval_jobs(conn, n=1, threshold=70, seed=1)
    conn.close()
    out = data_home / "pairs.yaml"
    write_sample_pairs(out, jobs)
    text = out.read_text(encoding="utf-8")
    assert "human_score" in text
    assert "Fill human_score" in text
    data = yaml.safe_load(text.split("\n", 1)[1])
    pair = data["pairs"][0]
    assert "score" not in pair
    assert pair["human_score"] is None
    assert pair["id"] == "job-a"


def test_cli_sample_refuses_overwrite(data_home: Path) -> None:
    conn = connect(data_home / "argospipe.db")
    _seed_match(conn, "job-a", 80)
    conn.commit()
    conn.close()
    out = data_home / "pairs.yaml"
    out.write_text("existing", encoding="utf-8")
    result = runner.invoke(app, ["eval", "sample", "--out", str(out), "--n", "1"])
    assert result.exit_code == 1
    assert "--force" in result.output
    assert out.read_text(encoding="utf-8") == "existing"


def test_cli_sample_force_overwrites(data_home: Path) -> None:
    conn = connect(data_home / "argospipe.db")
    _seed_match(conn, "job-a", 80)
    conn.commit()
    conn.close()
    out = data_home / "pairs.yaml"
    out.write_text("existing", encoding="utf-8")
    result = runner.invoke(app, ["eval", "sample", "--out", str(out), "--n", "1", "--force"])
    assert result.exit_code == 0, result.output
    assert "human_score" in out.read_text(encoding="utf-8")


def test_cli_sample_empty_database_exits(data_home: Path) -> None:
    result = runner.invoke(app, ["eval", "sample", "--n", "5"])
    assert result.exit_code == 1
    assert "No jobs" in result.output


def test_load_pairs_rejects_null_human_score(tmp_path: Path) -> None:
    pairs = tmp_path / "pairs.yaml"
    pairs.write_text(
        "pairs:\n  - id: a\n    job: {title: a, company: Acme}\n    human_score: null\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="1 pairs have no human_score yet"):
        load_pairs(pairs)


def test_cli_sample_uses_profile_threshold(data_home: Path) -> None:
    save_profile(Profile(preferences=Preferences(threshold=60)))
    conn = connect(data_home / "argospipe.db")
    _seed_match(conn, "high", 76)
    _seed_match(conn, "border", 65)
    conn.commit()
    conn.close()
    out = data_home / "pairs.yaml"
    result = runner.invoke(
        app, ["eval", "sample", "--out", str(out), "--n", "2", "--seed", "1", "--force"]
    )
    assert result.exit_code == 0, result.output
    data = yaml.safe_load(out.read_text(encoding="utf-8").split("\n", 1)[1])
    ids = {pair["id"] for pair in data["pairs"]}
    assert ids == {"high", "border"}


def test_sample_fills_shortfall_with_prefiltered_and_skips_empty_descriptions(
    data_home: Path,
) -> None:
    conn = connect(data_home / "argospipe.db")
    _seed_match(conn, "job-1", 90)
    run_id = start_run(conn, "2026-01-02")
    for index in range(5):
        upsert_job(conn, _job(f"pre-{index}"), "2026-01-02")
        record_run_job(conn, run_id, f"pre-{index}", "prefiltered_out", ["lang"])
    no_description = _job("pre-empty").model_copy(update={"description": None})
    upsert_job(conn, no_description, "2026-01-02")
    record_run_job(conn, run_id, "pre-empty", "prefiltered_out", ["lang"])

    sampled = sample_eval_jobs(conn, n=10, threshold=70, seed=1)
    conn.close()

    fingerprints = {job.fingerprint for job in sampled}
    assert len(sampled) == 6
    assert "pre-empty" not in fingerprints


def test_cli_eval_without_model_exits(data_home: Path) -> None:
    pairs = data_home / "pairs.yaml"
    pairs.write_text("pairs: []\n", encoding="utf-8")

    result = runner.invoke(app, ["eval", "--pairs", str(pairs)])

    assert result.exit_code == 1
    assert "--model" in result.output


def test_sample_fills_from_matches_when_no_prefiltered(data_home: Path) -> None:
    conn = connect(data_home / "argospipe.db")
    for index in range(12):
        _seed_match(conn, f"job-{index}", 95)

    sampled = sample_eval_jobs(conn, n=10, threshold=70)
    again = sample_eval_jobs(conn, n=10, threshold=70)
    conn.close()

    assert len(sampled) == 10
    assert [job.fingerprint for job in sampled] == [job.fingerprint for job in again]
