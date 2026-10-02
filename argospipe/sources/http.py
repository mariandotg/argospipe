import asyncio
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from argospipe import __version__


class SourceNotFoundError(Exception):
    """The requested job board does not exist."""


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            try:
                date = parsedate_to_datetime(retry_after)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=UTC)
                return max(0.0, (date - datetime.now(UTC)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return float(2**attempt)


class SourceHTTPClient:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        semaphore: asyncio.Semaphore | None = None,
    ) -> None:
        self._semaphore = semaphore if semaphore is not None else asyncio.Semaphore(2)
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": f"argospipe/{__version__} (+https://github.com/mariandotg/argospipe)"
            },
            timeout=30.0,
            transport=transport,
        )

    async def get(self, url: str) -> httpx.Response:
        async with self._semaphore:
            for attempt in range(4):
                response = await self._client.get(url)
                if response.status_code == 404:
                    raise SourceNotFoundError(f"Source not found: {url}")
                retryable = response.status_code == 429 or 500 <= response.status_code <= 599
                if retryable and attempt < 3:
                    await asyncio.sleep(_retry_delay(response, attempt))
                    continue
                response.raise_for_status()
                return response
        raise AssertionError("Retry loop ended without a response")

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "SourceHTTPClient":
        return self

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        await self.aclose()
