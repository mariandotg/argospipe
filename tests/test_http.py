import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from argospipe.sources.http import SourceHTTPClient, SourceNotFoundError


def test_request_identifies_client_and_retries_429(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"jobs": []})

    sleep = AsyncMock()
    monkeypatch.setattr("argospipe.sources.http.asyncio.sleep", sleep)

    async def run() -> None:
        async with SourceHTTPClient(transport=httpx.MockTransport(respond)) as client:
            assert (await client.get("https://example.com/jobs")).status_code == 200

    asyncio.run(run())
    assert len(requests) == 2
    assert requests[0].headers["User-Agent"] == (
        "argospipe/0.1.0 (+https://github.com/mariandotg/argospipe)"
    )
    sleep.assert_awaited_once_with(2.0)


def test_500_fails_after_three_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(500)

    sleep = AsyncMock()
    monkeypatch.setattr("argospipe.sources.http.asyncio.sleep", sleep)

    async def run() -> None:
        async with SourceHTTPClient(transport=httpx.MockTransport(respond)) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await client.get("https://example.com/jobs")

    asyncio.run(run())
    assert requests == 4
    assert [call.args[0] for call in sleep.await_args_list] == [1.0, 2.0, 4.0]


def test_404_has_dedicated_error() -> None:
    async def run() -> None:
        async with SourceHTTPClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(404))
        ) as client:
            with pytest.raises(SourceNotFoundError):
                await client.get("https://example.com/unknown")

    asyncio.run(run())


def test_requests_share_two_request_limit() -> None:
    async def run() -> None:
        active = 0
        maximum = 0
        first_two_started = asyncio.Event()
        release = asyncio.Event()

        async def respond(request: httpx.Request) -> httpx.Response:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                first_two_started.set()
            await release.wait()
            active -= 1
            return httpx.Response(200)

        async with SourceHTTPClient(transport=httpx.MockTransport(respond)) as client:
            requests = [
                asyncio.create_task(client.get("https://example.com/jobs")) for _ in range(3)
            ]
            await first_two_started.wait()
            await asyncio.sleep(0)
            release.set()
            await asyncio.gather(*requests)
        assert maximum == 2

    asyncio.run(run())
