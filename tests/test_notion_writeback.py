import asyncio
import json
from typing import Any

import httpx
import pytest

from argospipe.config import NotionFields, NotionSourceConfig
from argospipe.core.models import (
    Discard,
    JobRecord,
    OfferLink,
    RunMatch,
    RunResult,
    Unscored,
)
from argospipe.llm.schemas import MatchResult
from argospipe.output.notion import NotionWritebackError, writeback

SCHEMA = {
    "Argos score": {"type": "number"},
    "Argos estado": {"type": "select"},
    "Argos motivo": {"type": "rich_text"},
    "Argos resumen": {"type": "rich_text"},
    "Argos gaps": {"type": "rich_text"},
    "Name": {"type": "title"},
}
EMPTY_PAGE: dict[str, Any] = {
    "properties": {
        "Argos score": {"type": "number", "number": None},
        "Argos estado": {"type": "select", "select": None},
        "Argos motivo": {"type": "rich_text", "rich_text": []},
        "Argos resumen": {"type": "rich_text", "rich_text": []},
        "Argos gaps": {"type": "rich_text", "rich_text": []},
    }
}


def _config() -> NotionSourceConfig:
    return NotionSourceConfig(
        database_id="db-1",
        fields=NotionFields(
            title="Name", company="Company", url="Link", description="Description", location="Geo"
        ),
    )


def _job(fingerprint: str) -> JobRecord:
    return JobRecord(
        fingerprint=fingerprint,
        company="Acme",
        title="Engineer",
        first_seen="2026-10-01",
        last_seen="2026-10-01",
    )


def _result() -> RunResult:
    def match(fingerprint: str, score: int) -> RunMatch:
        return RunMatch(
            job=_job(fingerprint),
            result=MatchResult(
                score=score,
                gaps=["no Go", "no k8s"],
                seniority_match="match",
                summary="Good fit " + "x" * 2500,
            ),
        )

    fingerprints = ["hi", "low", "pre", "rank", "nodesc", "fail", "other"]
    links = [
        OfferLink(fingerprint=f, source="notion:db-1", external_id=f"page-{f}")
        for f in fingerprints
    ]
    links[-1] = OfferLink(fingerprint="other", source="notion:db-2", external_id="page-other")
    links.append(OfferLink(fingerprint="file", source="file:jobs.csv", external_id="1"))
    return RunResult(
        run_id=1,
        started_at="2026-10-01T00:00:00Z",
        matches=[match("hi", 85), match("low", 40), match("other", 90), match("file", 90)],
        discards=[
            Discard(fingerprint="pre", stage="prefiltered_out", reasons=["onsite", "junior"]),
            Discard(fingerprint="rank", stage="ranked_out", reasons=["weak stack"]),
        ],
        unscored=[
            Unscored(fingerprint="nodesc", kind="missing_description"),
            Unscored(fingerprint="fail", kind="failed", reasons=["LLM timeout"]),
        ],
        links=links,
    )


class Fake:
    def __init__(
        self, schema: dict[str, Any] = SCHEMA, pages: dict[str, Any] | None = None
    ) -> None:
        self.schema = schema
        self.pages = pages or {}
        self.calls: list[tuple[str, str]] = []
        self.patches: dict[str, dict[str, Any]] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        if request.method == "GET" and path == "/v1/databases/db-1":
            return httpx.Response(200, json={"properties": self.schema})
        page_id = path.rsplit("/", 1)[-1]
        if request.method == "GET":
            return httpx.Response(200, json=self.pages.get(page_id, EMPTY_PAGE))
        body = json.loads(request.content)
        self.patches[page_id] = body["properties"]
        return httpx.Response(200, json={})

    @property
    def patch_count(self) -> int:
        return sum(1 for method, _ in self.calls if method == "PATCH")


def _run(fake: Fake, result: RunResult | None = None) -> Any:
    return asyncio.run(
        writeback(
            result or _result(),
            _config(),
            threshold=70,
            token="t",
            transport=httpx.MockTransport(fake),
        )
    )


def _text(prop: dict[str, Any]) -> str:
    return "".join(s["text"]["content"] for s in prop["rich_text"])


def test_payload_per_estado_and_only_five_properties() -> None:
    fake = Fake()
    report = _run(fake)

    assert sorted(fake.patches) == [
        "page-fail",
        "page-hi",
        "page-low",
        "page-nodesc",
        "page-pre",
        "page-rank",
    ]
    assert report.errors == []
    for props in fake.patches.values():
        assert set(props) == {
            "Argos score",
            "Argos estado",
            "Argos motivo",
            "Argos resumen",
            "Argos gaps",
        }

    hi = fake.patches["page-hi"]
    assert hi["Argos score"] == {"number": 85}
    assert hi["Argos estado"] == {"select": {"name": "recomendada"}}
    assert hi["Argos motivo"]["rich_text"] == []
    assert hi["Argos gaps"]["rich_text"][0]["text"]["content"] == "no Go; no k8s"
    segments = hi["Argos resumen"]["rich_text"]
    assert [len(s["text"]["content"]) for s in segments] == [2000, 509]

    low = fake.patches["page-low"]
    assert low["Argos score"] == {"number": 40}
    assert low["Argos estado"] == {"select": {"name": "descartada"}}
    assert "40" in _text(low["Argos motivo"])

    pre = fake.patches["page-pre"]
    assert pre["Argos score"] == {"number": None}
    assert pre["Argos estado"] == {"select": {"name": "descartada"}}
    assert _text(pre["Argos motivo"]) == "onsite; junior"
    assert fake.patches["page-rank"]["Argos estado"] == {"select": {"name": "descartada"}}
    assert fake.patches["page-nodesc"]["Argos estado"] == {"select": {"name": "sin descripción"}}
    assert fake.patches["page-fail"]["Argos estado"] == {"select": {"name": "fallida"}}
    assert _text(fake.patches["page-fail"]["Argos motivo"]) == "LLM timeout"


def test_non_notion_and_other_database_offers_are_ignored() -> None:
    fake = Fake()
    _run(fake)
    paths = {path for _, path in fake.calls}
    assert "/v1/pages/page-other" not in paths
    assert "/v1/pages/1" not in paths


@pytest.mark.parametrize(
    "broken, expected",
    [
        ({k: v for k, v in SCHEMA.items() if k != "Argos gaps"}, "'Argos gaps' (missing"),
        ({**SCHEMA, "Argos score": {"type": "rich_text"}}, "'Argos score' (is rich_text"),
    ],
)
def test_bad_schema_raises_and_writes_nothing(broken: dict[str, Any], expected: str) -> None:
    fake = Fake(schema=broken)
    with pytest.raises(NotionWritebackError, match="lacks write-back properties") as error:
        _run(fake)
    assert expected in str(error.value)
    assert fake.calls == [("GET", "/v1/databases/db-1")]


def test_second_run_with_same_values_sends_no_patch() -> None:
    first = Fake()
    _run(first)
    pages = {
        page_id: {
            "properties": {
                "Argos score": {"number": props["Argos score"]["number"]},
                "Argos estado": {"select": props["Argos estado"]["select"]},
                **{
                    name: {
                        "rich_text": [
                            {"plain_text": s["text"]["content"]} for s in props[name]["rich_text"]
                        ]
                    }
                    for name in ("Argos motivo", "Argos resumen", "Argos gaps")
                },
            }
        }
        for page_id, props in first.patches.items()
    }
    second = Fake(pages=pages)
    report = _run(second)

    assert second.patch_count == 0
    assert len(report.unchanged) == 6
    assert report.updated == []


def test_retries_on_429() -> None:
    fake = Fake()
    state = {"limited": False}

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH" and not state["limited"]:
            state["limited"] = True
            return httpx.Response(429, headers={"Retry-After": "0"})
        return fake(request)

    result = _result()
    result.links = [link for link in result.links if link.external_id == "page-hi"]
    report = asyncio.run(
        writeback(result, _config(), token="t", transport=httpx.MockTransport(respond))
    )
    assert report.updated == ["page-hi"]
    assert fake.patch_count == 1
