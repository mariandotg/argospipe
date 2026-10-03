import random
import sqlite3
from pathlib import Path

import yaml

from argospipe.db.repo import (
    EvalSampleJob,
    count_jobs,
    list_latest_matched_jobs,
    list_prefiltered_jobs,
)

BUCKET_MARGIN = 15
PREFILTERED_MAX_FRACTION = 0.2

PAIRS_FILE_HEADER = (
    "# Fill human_score (0-100) and comment for each pair before running `argospipe eval`.\n"
)


def eval_threshold_from_profile(threshold: int | None) -> int:
    return threshold if threshold is not None else 70


def classify_match_score(score: int, threshold: int) -> str:
    if score >= threshold + BUCKET_MARGIN:
        return "high"
    if score < threshold - BUCKET_MARGIN:
        return "low"
    return "borderline"


def _take_from_bucket(
    bucket: list[EvalSampleJob], count: int, rng: random.Random
) -> list[EvalSampleJob]:
    if count <= 0 or not bucket:
        return []
    items = bucket.copy()
    rng.shuffle(items)
    return items[:count]


def _fill_shortfall(
    selected: list[EvalSampleJob],
    pools: dict[str, list[EvalSampleJob]],
    target: int,
    rng: random.Random,
) -> list[EvalSampleJob]:
    chosen = {job.fingerprint for job in selected}
    remainder: list[EvalSampleJob] = []
    for jobs in pools.values():
        for job in jobs:
            if job.fingerprint not in chosen:
                remainder.append(job)
    remainder.sort(key=lambda job: job.fingerprint)
    rng.shuffle(remainder)
    for job in remainder:
        if len(selected) >= target:
            break
        selected.append(job)
        chosen.add(job.fingerprint)
    return selected


def sample_eval_jobs(
    conn: sqlite3.Connection,
    n: int,
    threshold: int,
    seed: int | None = None,
) -> list[EvalSampleJob]:
    if count_jobs(conn) == 0:
        raise ValueError("No jobs in the database. Run `argospipe run` first.")

    rng = random.Random(0 if seed is None else seed)
    prefiltered_cap = int(n * PREFILTERED_MAX_FRACTION)
    matched_target = n - prefiltered_cap

    matched_jobs = list_latest_matched_jobs(conn)
    buckets: dict[str, list[EvalSampleJob]] = {"high": [], "borderline": [], "low": []}
    for job in matched_jobs:
        assert job.score is not None
        buckets[classify_match_score(job.score, threshold)].append(job)

    per_bucket = matched_target // 3
    extra = matched_target % 3
    targets = {
        "high": per_bucket + (1 if extra > 0 else 0),
        "borderline": per_bucket + (1 if extra > 1 else 0),
        "low": per_bucket,
    }

    selected: list[EvalSampleJob] = []
    chosen: set[str] = set()
    for name in ("high", "borderline", "low"):
        picked = _take_from_bucket(buckets[name], targets[name], rng)
        for job in picked:
            if job.fingerprint in chosen:
                continue
            selected.append(job)
            chosen.add(job.fingerprint)

    if len(selected) < matched_target:
        pools = {
            name: [job for job in jobs if job.fingerprint not in chosen]
            for name, jobs in buckets.items()
        }
        selected = _fill_shortfall(selected, pools, matched_target, rng)
        chosen = {job.fingerprint for job in selected}

    prefiltered = list_prefiltered_jobs(conn, chosen)
    prefiltered_picked = _take_from_bucket(
        prefiltered, max(prefiltered_cap, n - len(selected)), rng
    )
    selected.extend(prefiltered_picked)

    if len(selected) < n:
        chosen = {job.fingerprint for job in selected}
        pools = {
            name: [job for job in jobs if job.fingerprint not in chosen]
            for name, jobs in buckets.items()
        }
        selected = _fill_shortfall(selected, pools, n, rng)

    if seed is not None:
        rng.shuffle(selected)
    else:
        selected.sort(key=lambda job: job.fingerprint)
    return selected


def job_to_pair_dict(job: EvalSampleJob) -> dict[str, object]:
    return {
        "id": job.fingerprint,
        "job": {
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "description": job.description,
        },
        "profile": None,
        "human_score": None,
        "comment": "",
    }


def write_sample_pairs(path: Path, jobs: list[EvalSampleJob]) -> None:
    payload = {"pairs": [job_to_pair_dict(job) for job in jobs]}
    body = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(PAIRS_FILE_HEADER + body, encoding="utf-8")


def sample_pairs_to_file(
    conn: sqlite3.Connection,
    path: Path,
    n: int,
    threshold: int,
    seed: int | None,
    force: bool,
) -> int:
    if path.exists() and not force:
        raise FileExistsError(f"Output already exists: {path}. Use --force to overwrite it.")
    jobs = sample_eval_jobs(conn, n, threshold, seed)
    write_sample_pairs(path, jobs)
    return len(jobs)
