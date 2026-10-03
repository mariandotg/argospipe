import asyncio
from collections.abc import Callable
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from argospipe.config import Config, Profile
from argospipe.core.fingerprint import fingerprint
from argospipe.core.models import JobRecord
from argospipe.llm.common import LLMOutputError
from argospipe.llm.provider import LLMProvider, Usage, cost_usd

ProviderFactory = Callable[[str], LLMProvider]


class EvalJob(BaseModel):
    title: str
    company: str
    location: str | None = None
    description: str | None = None


class EvalPair(BaseModel):
    id: str
    job: EvalJob
    profile: Profile | None = None
    human_score: int = Field(ge=0, le=100)
    comment: str = ""


class PairsFile(BaseModel):
    pairs: list[EvalPair]


class PairRow(BaseModel):
    id: str
    human_score: int
    model_score: int | None = None
    error: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0


class ModelResult(BaseModel):
    model: str
    mae: float | None
    agreement_pct: float | None
    failures: int
    tokens_in: int
    tokens_out: int
    cost_usd: float | None
    rows: list[PairRow]


class EvalResult(BaseModel):
    threshold: int
    pairs: int
    models: list[ModelResult]


def load_pairs(path: Path) -> list[EvalPair]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    pairs_raw = data.get("pairs", []) if isinstance(data, dict) else []
    if isinstance(pairs_raw, list):
        missing = sum(
            1 for pair in pairs_raw if isinstance(pair, dict) and pair.get("human_score") is None
        )
        if missing:
            raise ValueError(f"{missing} pairs have no human_score yet: fill them in {path}")
    return PairsFile.model_validate(data).pairs


def _record(job: EvalJob) -> JobRecord:
    return JobRecord(
        fingerprint=fingerprint(job.company, job.title, job.location),
        company=job.company,
        title=job.title,
        location=job.location,
        description=job.description,
        first_seen="",
        last_seen="",
    )


async def _run_pair(
    provider: LLMProvider, pair: EvalPair, default_profile: Profile | None, sem: asyncio.Semaphore
) -> PairRow:
    profile = pair.profile or default_profile
    if profile is None:
        return PairRow(
            id=pair.id,
            human_score=pair.human_score,
            error="no profile: pair has none and profile.yaml is missing",
        )
    async with sem:
        try:
            result, usage = await provider.match(
                profile.profile, profile.preferences, _record(pair.job)
            )
        except Exception as exc:
            usage = (
                exc.usage if isinstance(exc, LLMOutputError) else Usage(tokens_in=0, tokens_out=0)
            )
            return PairRow(
                id=pair.id,
                human_score=pair.human_score,
                error=f"{type(exc).__name__}: {exc}",
                tokens_in=usage.tokens_in,
                tokens_out=usage.tokens_out,
            )
    return PairRow(
        id=pair.id,
        human_score=pair.human_score,
        model_score=result.score,
        tokens_in=usage.tokens_in,
        tokens_out=usage.tokens_out,
    )


def _summarize(model: str, rows: list[PairRow], threshold: int, config: Config) -> ModelResult:
    scored = [(r.model_score, r.human_score) for r in rows if r.model_score is not None]
    mae = agreement = None
    if scored:
        mae = sum(abs(m - h) for m, h in scored) / len(scored)
        agree = sum((m >= threshold) == (h >= threshold) for m, h in scored)
        agreement = 100 * agree / len(scored)
    usage = Usage(
        tokens_in=sum(r.tokens_in for r in rows), tokens_out=sum(r.tokens_out for r in rows)
    )
    price = config.pricing.get(model)
    return ModelResult(
        model=model,
        mae=mae,
        agreement_pct=agreement,
        failures=len(rows) - len(scored),
        tokens_in=usage.tokens_in,
        tokens_out=usage.tokens_out,
        cost_usd=cost_usd(usage, price) if price else None,
        rows=rows,
    )


async def run_eval(
    pairs: list[EvalPair],
    models: list[str],
    factory: ProviderFactory,
    config: Config,
    default_profile: Profile | None,
    threshold: int,
    concurrency: int = 4,
) -> EvalResult:
    sem = asyncio.Semaphore(concurrency)
    results = []
    for model in models:
        provider = factory(model)
        rows = await asyncio.gather(
            *(_run_pair(provider, pair, default_profile, sem) for pair in pairs)
        )
        results.append(_summarize(model, list(rows), threshold, config))
    return EvalResult(threshold=threshold, pairs=len(pairs), models=results)
