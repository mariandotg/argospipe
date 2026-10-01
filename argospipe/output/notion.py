import asyncio
import os
from typing import Any

import httpx
from pydantic import BaseModel

from argospipe.config import NotionSourceConfig
from argospipe.core.models import RunResult
from argospipe.sources.http import _retry_delay
from argospipe.sources.notion import _NOTION_VERSION

_API = "https://api.notion.com/v1"
_MAX_SEGMENT = 2000
_CONCURRENCY = 3

_RECOMMENDED = "recomendada"
_DISCARDED = "descartada"
_NO_DESCRIPTION = "sin descripción"
_FAILED = "fallida"


class NotionWritebackError(ValueError):
    """The database schema does not match what argospipe writes."""


class WritebackReport(BaseModel):
    updated: list[str] = []
    unchanged: list[str] = []
    errors: list[str] = []


class _Row(BaseModel):
    score: int | None
    status: str
    reason: str
    summary: str
    gaps: str


def _rows(result: RunResult, threshold: int) -> dict[str, _Row]:
    rows: dict[str, _Row] = {}
    for discard in result.discards:
        rows[discard.fingerprint] = _Row(
            score=None,
            status=_DISCARDED,
            reason="; ".join(discard.reasons),
            summary="",
            gaps="",
        )
    for item in result.unscored:
        status = _NO_DESCRIPTION if item.kind == "missing_description" else _FAILED
        rows[item.fingerprint] = _Row(
            score=None, status=status, reason="; ".join(item.reasons), summary="", gaps=""
        )
    for match in result.matches:
        score = match.result.score
        recommended = score >= threshold
        rows[match.job.fingerprint] = _Row(
            score=score,
            status=_RECOMMENDED if recommended else _DISCARDED,
            reason="" if recommended else f"score {score} below threshold {threshold}",
            summary=match.result.summary,
            gaps="; ".join(match.result.gaps),
        )
    return rows


def _rich_text(text: str) -> list[dict[str, Any]]:
    return [
        {"type": "text", "text": {"content": text[start : start + _MAX_SEGMENT]}}
        for start in range(0, len(text), _MAX_SEGMENT)
    ]


def _payload(row: _Row, config: NotionSourceConfig) -> dict[str, Any]:
    names = config.writeback
    return {
        names.score: {"number": row.score},
        names.status: {"select": {"name": row.status}},
        names.reason: {"rich_text": _rich_text(row.reason)},
        names.summary: {"rich_text": _rich_text(row.summary)},
        names.gaps: {"rich_text": _rich_text(row.gaps)},
    }


def _plain(segments: Any) -> str:
    if not isinstance(segments, list):
        return ""
    return "".join(
        s["plain_text"]
        for s in segments
        if isinstance(s, dict) and isinstance(s.get("plain_text"), str)
    )


def _current(properties: dict[str, Any], config: NotionSourceConfig) -> _Row | None:
    names = config.writeback
    try:
        score = properties[names.score]["number"]
        select = properties[names.status]["select"]
        return _Row(
            score=score,
            status=select["name"] if isinstance(select, dict) else "",
            reason=_plain(properties[names.reason]["rich_text"]),
            summary=_plain(properties[names.summary]["rich_text"]),
            gaps=_plain(properties[names.gaps]["rich_text"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _check_schema(database: dict[str, Any], config: NotionSourceConfig) -> None:
    names = config.writeback
    expected = {
        names.score: "number",
        names.status: "select",
        names.reason: "rich_text",
        names.summary: "rich_text",
        names.gaps: "rich_text",
    }
    properties: dict[str, Any] = database.get("properties", {})
    problems = []
    for name, kind in expected.items():
        found = properties.get(name)
        if not isinstance(found, dict):
            problems.append(f"{name!r} (missing, expected {kind})")
        elif found.get("type") != kind:
            problems.append(f"{name!r} (is {found.get('type')}, expected {kind})")
    if problems:
        raise NotionWritebackError(
            f"Notion database {config.database_id} lacks write-back properties: "
            + ", ".join(problems)
        )


async def _send(client: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> Any:
    for attempt in range(4):
        response = await client.request(method, url, **kwargs)
        if response.status_code == 429 and attempt < 3:
            await asyncio.sleep(_retry_delay(response, attempt))
            continue
        response.raise_for_status()
        return response.json()


async def writeback(
    result: RunResult,
    config: NotionSourceConfig,
    *,
    threshold: int = 70,
    token: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> WritebackReport:
    token = token if token is not None else os.environ.get("NOTION_TOKEN")
    if not token:
        raise ValueError("NOTION_TOKEN is required to write back to Notion")

    source = f"notion:{config.database_id}"
    rows = _rows(result, threshold)
    targets: dict[str, _Row] = {}
    for link in result.links:
        if link.source == source and link.fingerprint in rows:
            targets[link.external_id] = rows[link.fingerprint]

    report = WritebackReport()
    headers = {"Authorization": f"Bearer {token}", "Notion-Version": _NOTION_VERSION}
    async with httpx.AsyncClient(headers=headers, timeout=30.0, transport=transport) as client:
        _check_schema(await _send(client, "GET", f"{_API}/databases/{config.database_id}"), config)
        semaphore = asyncio.Semaphore(_CONCURRENCY)

        async def sync(page_id: str, row: _Row) -> None:
            try:
                async with semaphore:
                    page = await _send(client, "GET", f"{_API}/pages/{page_id}")
                    if _current(page.get("properties", {}), config) == row:
                        report.unchanged.append(page_id)
                        return
                    await _send(
                        client,
                        "PATCH",
                        f"{_API}/pages/{page_id}",
                        json={"properties": _payload(row, config)},
                    )
                report.updated.append(page_id)
            except httpx.HTTPError as error:
                report.errors.append(f"page {page_id}: {error}")

        await asyncio.gather(*(sync(page_id, row) for page_id, row in targets.items()))
    return report
