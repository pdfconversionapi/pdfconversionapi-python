"""Retry decisions (SPEC section 8), alone and through both clients."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

from pdfconversionapi import (
    APIConnectionError,
    APITimeoutError,
    AsyncPdfConversionApi,
    BadRequestError,
    InternalServerError,
    PdfConversionApi,
    RateLimitError,
    ServiceUnavailableError,
)
from pdfconversionapi._core import RetryState, jitter

BASE = "https://api.test"
OK = {
    "ConversionCost": 1,
    "ConversionTime": 0.1,
    "Files": [{"FileName": "a.pdf", "FileSize": 1, "FileData": "eA=="}],
}


def error_response(status: int, retry_after: str | None = None) -> httpx.Response:
    headers = {"x-request-id": f"r{status}"}
    if retry_after is not None:
        headers["retry-after"] = retry_after
    return httpx.Response(status, json={"Code": status, "Message": f"m{status}"}, headers=headers)


Step = Any  # an httpx.Response, or an exception to raise


class Script:
    """A transport that answers with the given steps, in order, and counts requests."""

    def __init__(self, *steps: Step) -> None:
        self.steps = list(steps)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        if isinstance(step, Exception):
            raise step
        assert isinstance(step, httpx.Response)
        return step


def sync_client(script: Script, waits: list[float], **options: Any) -> PdfConversionApi:
    return PdfConversionApi(
        api_key="k" * 20,
        base_url=BASE,
        http_client=httpx.Client(transport=httpx.MockTransport(script)),
        sleep=waits.append,
        **options,
    )


def convert(client: PdfConversionApi) -> Any:
    return client.convert("txt", "pdf", b"x")


# RetryState alone


def fixed(value: float) -> Callable[[], float]:
    return lambda: value


def test_jitter_bounds() -> None:
    assert jitter(1, fixed(0.0)) == 0
    assert jitter(1, fixed(0.999)) < 2
    assert jitter(4, fixed(0.999)) < 16
    assert jitter(3, fixed(0.5)) == 4


def test_no_response_retries_until_attempts_run_out() -> None:
    state = RetryState(4, 60, fixed(0.5))
    waits = []
    while True:
        state.next_attempt()
        wait = state.after_no_response()
        if wait is None:
            break
        waits.append(wait)
    assert waits == [1, 2, 4, 8]
    assert state.attempt == 5


@pytest.mark.parametrize(
    ("status", "header", "expected"),
    [
        (429, "10", 11),
        (503, "5", 6),
        (429, None, 2),
        (503, "soon", 2),
        (429, "60", 61),
        (429, "61", None),
        (503, "600", None),
        (400, None, None),
        (401, None, None),
        (403, None, None),
        (404, None, None),
        (413, None, None),
        (422, None, None),
        (502, None, None),
        (504, "1", None),
        (200, None, None),
    ],
)
def test_after_status(status: int, header: str | None, expected: float | None) -> None:
    state = RetryState(4, 60, fixed(0.5))
    state.next_attempt()
    assert state.after_status(status, header) == expected


def test_500_is_retried_once_per_call() -> None:
    state = RetryState(4, 60, fixed(0.0))
    state.next_attempt()
    assert state.after_status(500, None) == 1
    state.next_attempt()
    assert state.after_status(503, "1") == 1
    state.next_attempt()
    assert state.after_status(500, None) is None


def test_no_retries_when_max_retries_is_zero() -> None:
    state = RetryState(0, 60)
    state.next_attempt()
    assert state.after_no_response() is None
    assert state.after_status(503, "1") is None
    assert state.after_status(500, None) is None


def test_the_last_attempt_is_never_retried() -> None:
    state = RetryState(1, 60, fixed(0.0))
    state.next_attempt()
    assert state.after_status(429, "1") == 1
    state.next_attempt()
    assert state.after_status(429, "1") is None


# Through the sync client


def test_connect_timeout_and_errors_are_retried() -> None:
    waits: list[float] = []
    script = Script(
        httpx.ConnectTimeout("t"), httpx.ConnectError("refused"), httpx.Response(200, json=OK)
    )
    result = convert(sync_client(script, waits))
    assert result.file.data == b"x"
    assert len(script.requests) == 3
    assert len(waits) == 2
    assert 0 <= waits[0] < 2
    assert 0 <= waits[1] < 4


def test_a_connection_that_never_answers() -> None:
    waits: list[float] = []
    script = Script(httpx.RemoteProtocolError("Server disconnected without sending a response."))
    with pytest.raises(APIConnectionError, match=r"no response from api.test after 5 attempts"):
        convert(sync_client(script, waits))
    assert len(script.requests) == 5
    assert len(waits) == 4


def test_no_retry_when_retries_are_off() -> None:
    waits: list[float] = []
    script = Script(httpx.ReadError("reset"), httpx.Response(200, json=OK))
    with pytest.raises(APIConnectionError, match="after 1 attempt:"):
        convert(sync_client(script, waits, max_retries=0))
    assert waits == []


@pytest.mark.parametrize(
    "exc", [httpx.ReadTimeout("slow"), httpx.WriteTimeout("slow"), httpx.PoolTimeout("busy")]
)
def test_client_timeouts_are_never_retried(exc: Exception) -> None:
    waits: list[float] = []
    script = Script(exc, httpx.Response(200, json=OK))
    with pytest.raises(APITimeoutError, match="may still be running") as caught:
        convert(sync_client(script, waits))
    assert "within 240 seconds" in str(caught.value)
    assert len(script.requests) == 1
    assert waits == []


def test_timeout_message_outside_conversions() -> None:
    script = Script(httpx.ReadTimeout("slow"))
    with pytest.raises(APITimeoutError) as caught:
        sync_client(script, []).usage()
    assert "may still be running" not in str(caught.value)


def test_local_protocol_errors_are_not_retried() -> None:
    script = Script(httpx.LocalProtocolError("bad request"))
    with pytest.raises(APIConnectionError):
        convert(sync_client(script, []))
    assert len(script.requests) == 1


def test_retry_after_is_waited_plus_jitter() -> None:
    waits: list[float] = []
    script = Script(
        error_response(429, "10"), error_response(503, "5"), httpx.Response(200, json=OK)
    )
    convert(sync_client(script, waits))
    assert len(waits) == 2
    assert 10 <= waits[0] < 12
    assert 5 <= waits[1] < 9


def test_a_long_retry_after_is_raised_at_once() -> None:
    waits: list[float] = []
    script = Script(error_response(429, "61"))
    with pytest.raises(RateLimitError) as caught:
        convert(sync_client(script, waits))
    assert caught.value.retry_after == 61
    assert waits == []
    waits.clear()
    script = Script(error_response(429, "61"), httpx.Response(200, json=OK))
    convert(sync_client(script, waits, max_retry_wait=61))
    assert len(waits) == 1
    assert waits[0] >= 61


def test_gives_up_after_the_retries() -> None:
    waits: list[float] = []
    script = Script(error_response(503, "1"))
    with pytest.raises(ServiceUnavailableError) as caught:
        convert(sync_client(script, waits, max_retries=2))
    assert caught.value.retry_after == 1
    assert len(script.requests) == 3
    assert len(waits) == 2


def test_500_once_then_raised() -> None:
    waits: list[float] = []
    script = Script(error_response(500), error_response(503, "1"), error_response(500))
    with pytest.raises(InternalServerError):
        convert(sync_client(script, waits))
    assert len(script.requests) == 3
    assert waits[0] >= 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422, 504])
def test_other_errors_are_not_retried(status: int) -> None:
    script = Script(error_response(status))
    with pytest.raises(Exception, match=f"HTTP {status}"):
        convert(sync_client(script, []))
    assert len(script.requests) == 1


def chunks_then(error: Exception) -> Iterator[bytes]:
    yield b'{"ConversionCost":1,'
    raise error


def test_a_body_cut_short_is_not_retried() -> None:
    script = Script(
        httpx.Response(200, content=chunks_then(httpx.ReadError("reset"))),
        httpx.Response(200, json=OK),
    )
    with pytest.raises(APIConnectionError, match="while the response was being read"):
        convert(sync_client(script, []))
    assert len(script.requests) == 1


def test_a_body_timeout_is_a_timeout() -> None:
    script = Script(httpx.Response(200, content=chunks_then(httpx.ReadTimeout("slow"))))
    with pytest.raises(APITimeoutError):
        convert(sync_client(script, []))


def test_the_deadline_covers_the_whole_response(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr("pdfconversionapi._core.time.monotonic", lambda: float(next(clock)))

    def slow_body() -> Iterator[bytes]:
        yield b"{"
        yield b"}"

    script = Script(httpx.Response(200, content=slow_body()))
    with pytest.raises(APITimeoutError, match="within 150 seconds"):
        sync_client(script, [], timeout=150).usage()
    assert len(script.requests) == 1


def test_the_deadline_covers_the_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr("pdfconversionapi._core.time.monotonic", lambda: float(next(clock)))
    script = Script(httpx.Response(200, json=OK))
    with pytest.raises(APITimeoutError):
        sync_client(script, [], timeout=150).convert("txt", "pdf", b"x" * 300_000)


def test_each_request_carries_the_timeouts() -> None:
    script = Script(httpx.Response(200, json=OK))
    client = sync_client(script, [], timeout=30, connect_timeout=5)
    convert(client)
    client.convert("txt", "pdf", b"x", timeout=3)
    first, second = (r.extensions["timeout"] for r in script.requests)
    assert first == {"connect": 5, "read": 30, "write": 30, "pool": 30}
    assert second == {"connect": 3, "read": 3, "write": 3, "pool": 3}


# Through the async client


class AsyncScript(Script):
    async def handle(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        if isinstance(step, Exception):
            raise step
        assert isinstance(step, httpx.Response)
        return step


def async_client(script: AsyncScript, waits: list[float], **options: Any) -> AsyncPdfConversionApi:
    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    return AsyncPdfConversionApi(
        api_key="k" * 20,
        base_url=BASE,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(script.handle)),
        sleep=sleep,
        **options,
    )


@pytest.mark.anyio
async def test_async_retries() -> None:
    waits: list[float] = []
    script = AsyncScript(
        httpx.ConnectError("refused"),
        error_response(503, "2"),
        error_response(500),
        httpx.Response(200, json=OK),
    )
    result = await async_client(script, waits).convert("txt", "pdf", b"x")
    assert result.file.data == b"x"
    assert len(script.requests) == 4
    assert len(waits) == 3
    assert waits[1] >= 2
    assert waits[2] >= 1


@pytest.mark.anyio
async def test_async_timeouts_and_400s_are_not_retried() -> None:
    script = AsyncScript(httpx.ReadTimeout("slow"))
    with pytest.raises(APITimeoutError):
        await async_client(script, []).convert("txt", "pdf", b"x")
    script = AsyncScript(error_response(400))
    with pytest.raises(BadRequestError):
        await async_client(script, []).convert("txt", "pdf", b"x")
    assert len(script.requests) == 1


@pytest.mark.anyio
async def test_async_gives_up() -> None:
    waits: list[float] = []
    script = AsyncScript(httpx.RemoteProtocolError("closed"))
    with pytest.raises(APIConnectionError, match="after 5 attempts"):
        await async_client(script, waits).convert("txt", "pdf", b"x")
    assert len(waits) == 4


@pytest.mark.anyio
async def test_async_default_sleep_really_waits() -> None:
    from pdfconversionapi._async_client import _default_sleep

    await _default_sleep(0.001)
