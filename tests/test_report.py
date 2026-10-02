import re
from pathlib import Path

from argospipe.core.models import Discard, JobRecord, RunMatch, RunResult, SourceStatus
from argospipe.llm.schemas import FitReason, MatchResult
from argospipe.report.render import render


def _job(title: str, **kwargs: object) -> JobRecord:
    defaults = {
        "fingerprint": f"fp-{title}",
        "company": "Acme",
        "first_seen": "2026-10-01",
        "last_seen": "2026-10-01",
    }
    defaults.update(kwargs)
    return JobRecord(title=title, **defaults)  # type: ignore[arg-type]


def _match(title: str, score: int, **job_kwargs: object) -> RunMatch:
    return RunMatch(
        job=_job(title, **job_kwargs),
        result=MatchResult(
            score=score,
            seniority_match="match",
            summary=f"Summary for {title}",
            fit_reasons=[FitReason(reason="Stack", evidence="Python")],
        ),
    )


def _sample_result() -> RunResult:
    return RunResult(
        run_id=1,
        started_at="2026-10-01T12:00:00Z",
        finished_at="2026-10-01T12:05:00Z",
        new_count=10,
        discarded_count=3,
        missing_description_count=1,
        matched_count=4,
        tokens_in=1000,
        tokens_out=200,
        cost_usd=0.0123,
        matches=[
            _match("High", 85, url="https://example.com/high"),
            _match("Mid", 70),
            _match("Low", 50),
        ],
        discards=[
            Discard(fingerprint="fp-a", stage="prefiltered_out", reasons=["remote only"]),
            Discard(fingerprint="fp-b", stage="ranked_out", reasons=["low rank"]),
        ],
        sources=[
            SourceStatus(name="greenhouse", ok=True, fetched=5),
            SourceStatus(name="broken", ok=False, error="timeout", fetched=0),
        ],
    )


def test_recommended_split_and_order(tmp_path: Path) -> None:
    out = render(_sample_result(), tmp_path / "report.html", threshold=70)
    html = out.read_text(encoding="utf-8")

    recommended_pos = html.index("Recommended (score ≥ 70)")
    below_pos = html.index("Below threshold")
    discards_pos = html.index("Discards")
    assert recommended_pos < below_pos < discards_pos

    high_pos = html.index("High")
    mid_pos = html.index("Mid")
    low_pos = html.index("Low")
    assert high_pos < mid_pos
    assert mid_pos < below_pos
    assert low_pos > below_pos

    assert 'href="https://example.com/high"' in html
    assert html.count('class="card"') == 3


def test_untrusted_offer_text_is_escaped(tmp_path: Path) -> None:
    evil = _match("<script>alert(1)</script>", 90)
    result = RunResult(run_id=2, started_at="2026-10-01", matches=[evil])
    html = render(result, tmp_path / "r.html").read_text(encoding="utf-8")

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_self_contained_no_external_resources(tmp_path: Path) -> None:
    html = render(_sample_result(), tmp_path / "r.html").read_text(encoding="utf-8")
    lower = html.lower()

    assert "<link" not in lower
    assert re.search(r"<script\s+src", lower) is None
    assert "@import" not in lower
    assert "url(http" not in lower


def test_empty_run_renders(tmp_path: Path) -> None:
    result = RunResult(run_id=0, started_at="2026-10-01")
    path = render(result, tmp_path / "empty.html")
    html = path.read_text(encoding="utf-8")

    assert "argospipe run #0" in html
    assert "No offers met the threshold." in html
    assert "Below threshold" not in html
