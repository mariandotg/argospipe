import asyncio
import os
from datetime import UTC, datetime
from typing import Any

import httpx

from argospipe.config import NotionSourceConfig
from argospipe.sources.base import RawJob
from argospipe.sources.http import _retry_delay

_NOTION_VERSION = "2022-06-28"


def _text(properties: dict[str, Any], name: str, kind: str) -> str:
    value = properties.get(name, {})
    segments = value.get(kind, []) if isinstance(value, dict) else []
    if not isinstance(segments, list):
        return ""
    return "".join(
        segment.get("plain_text", "")
        for segment in segments
        if isinstance(segment, dict) and isinstance(segment.get("plain_text"), str)
    )


def _scalar(properties: dict[str, Any], name: str, kind: str) -> str | None:
    value = properties.get(name, {})
    result = value.get(kind) if isinstance(value, dict) else None
    return result if isinstance(result, str) else None


class NotionSource:
    config: NotionSourceConfig

    def __init__(
        self,
        config: NotionSourceConfig,
        token: str | None = None,
        since: datetime | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self.name = f"notion:{config.database_id}"
        self.errors: list[str] = []
        self._token = token if token is not None else os.environ.get("NOTION_TOKEN")
        self._since = since
        self._transport = transport

    async def fetch(self) -> list[RawJob]:
        self.errors.clear()
        if not self._token:
            raise ValueError("NOTION_TOKEN is required to fetch Notion jobs")

        url = f"https://api.notion.com/v1/databases/{self.config.database_id}/query"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": _NOTION_VERSION,
        }
        body: dict[str, Any] = {}
        if self._since is not None:
            body["filter"] = {
                "timestamp": "last_edited_time",
                "last_edited_time": {"on_or_after": _as_utc(self._since).isoformat()},
            }

        jobs: list[RawJob] = []
        async with httpx.AsyncClient(
            headers=headers, timeout=30.0, transport=self._transport
        ) as client:
            while True:
                for attempt in range(4):
                    response = await client.post(url, json=body)
                    if response.status_code == 429 and attempt < 3:
                        await asyncio.sleep(_retry_delay(response, attempt))
                        continue
                    response.raise_for_status()
                    break

                data: dict[str, Any] = response.json()
                for page in data["results"]:
                    job = self._parse_page(page)
                    if job is not None:
                        jobs.append(job)
                if not data["has_more"]:
                    break
                cursor = data["next_cursor"]
                if not cursor:
                    raise ValueError("Notion response has_more without next_cursor")
                body["start_cursor"] = cursor
        return jobs

    def _parse_page(self, page: dict[str, Any]) -> RawJob | None:
        properties: dict[str, Any] = page["properties"]
        status = properties.get("Status", {}).get("select")
        if isinstance(status, dict) and status.get("name") in self.config.skip_statuses:
            return None

        fields = self.config.fields
        title = _text(properties, fields.title, "title")
        company = _text(properties, fields.company, "rich_text")
        url = _scalar(properties, fields.url, "url")
        missing = [
            name
            for name, value in (("title", title), ("company", company), ("url", url))
            if not value or not value.strip()
        ]
        if missing:
            self.errors.append(
                f"page {page['id']}: missing required field(s): {', '.join(missing)}"
            )
            return None
        assert url is not None

        description = _text(properties, fields.description, "rich_text")
        location = _text(properties, fields.location, "rich_text")
        date = properties.get(fields.posted_at, {}) if fields.posted_at else {}
        posted_at = date.get("date", {}) if isinstance(date, dict) else {}
        return RawJob(
            title=title.strip(),
            company=company.strip(),
            url=url,
            description=description or None,
            location=location or None,
            posted_at=posted_at.get("start") if isinstance(posted_at, dict) else None,
            source=self.name,
            source_name=None,
            external_id=page["id"],
        )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
