from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Literal, TypedDict

from jinja2 import Environment, PackageLoader, select_autoescape

from argospipe.core.models import Discard, RunMatch, RunResult

_TEMPLATE = "template.html.j2"


class _DiscardGroup(TypedDict):
    reason: str
    count: int
    fingerprints: list[str]


def _split_matches(
    matches: list[RunMatch], threshold: int
) -> tuple[list[RunMatch], list[RunMatch]]:
    recommended = [m for m in matches if m.result.score >= threshold]
    below = [m for m in matches if m.result.score < threshold]

    def key(m: RunMatch) -> int:
        return m.result.score

    return sorted(recommended, key=key, reverse=True), sorted(below, key=key, reverse=True)


def _group_discards_by_reason(
    discards: list[Discard],
    stage: Literal["prefiltered_out", "ranked_out"],
) -> list[_DiscardGroup]:
    by_reason: dict[str, list[str]] = defaultdict(list)
    for discard in discards:
        if discard.stage != stage:
            continue
        reasons = discard.reasons or ["(no reason)"]
        for reason in reasons:
            by_reason[reason].append(discard.fingerprint)
    groups: list[_DiscardGroup] = [
        {"reason": reason, "count": len(fingerprints), "fingerprints": fingerprints}
        for reason, fingerprints in by_reason.items()
    ]
    groups.sort(key=lambda g: (-g["count"], g["reason"]))
    return groups


def _safe_url(url: str | None) -> str | None:
    """Offer URLs are untrusted: only http(s) links are rendered."""
    if url and url.strip().lower().startswith(("http://", "https://")):
        return url.strip()
    return None


def _environment() -> Environment:
    environment = Environment(
        loader=PackageLoader("argospipe.report", ""),
        autoescape=select_autoescape(enabled_extensions=("html", "j2")),
    )
    environment.filters["safe_url"] = _safe_url
    return environment


def render(result: RunResult, path: Path, *, threshold: int = 70) -> Path:
    recommended, below_threshold = _split_matches(result.matches, threshold)
    html = (
        _environment()
        .get_template(_TEMPLATE)
        .render(
            result=result,
            threshold=threshold,
            recommended=recommended,
            below_threshold=below_threshold,
            prefiltered_groups=_group_discards_by_reason(result.discards, "prefiltered_out"),
            ranked_groups=_group_discards_by_reason(result.discards, "ranked_out"),
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
