"""The asynchronous client: the same calls as PdfConversionApi, awaited."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import TypeVar

import httpx

from ._base import ClientBase
from ._core import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MAX_RETRY_WAIT,
    DEFAULT_TIMEOUT,
    Call,
    Deadline,
    ParamValue,
    ParsedConversion,
    RetryState,
    api_error,
    body_error,
    httpx_timeout,
    log_retry,
    parse_conversion,
    parse_conversions,
    parse_usage,
    request_headers,
    request_id,
    wait_after_send_error,
)
from ._files import discard_partial, safe_file_name, url_file_name
from ._inputs import FileInput
from ._models import AsyncConversionResult, AsyncOutputFile, Conversion, Usage
from ._multipart import MultipartBody

__all__ = ["AsyncPdfConversionApi"]

T = TypeVar("T")


async def _default_sleep(seconds: float) -> None:
    # anyio (an httpx dependency) sleeps under both asyncio and trio.
    try:
        import anyio
    except ImportError:  # pragma: no cover - httpx without anyio
        await asyncio.sleep(seconds)
    else:
        await anyio.sleep(seconds)


class AsyncPdfConversionApi(ClientBase):
    """The PDF Conversion API client for asyncio (and trio): every call is awaited.

    >>> async with AsyncPdfConversionApi() as client:    # reads PDFCONVERSIONAPI_KEY
    ...     result = await client.convert_file("report.docx", "pdf")
    ...     await result.save_all("out")

    The options are those of :class:`PdfConversionApi`, with an ``httpx.AsyncClient`` for
    ``http_client`` and an async ``sleep`` hook (for tests). Share one client between tasks.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        max_retry_wait: float = DEFAULT_MAX_RETRY_WAIT,
        http_client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            connect_timeout=connect_timeout,
            max_retries=max_retries,
            max_retry_wait=max_retry_wait,
        )
        if http_client is not None and not isinstance(http_client, httpx.AsyncClient):
            raise TypeError(
                f"http_client must be an httpx.AsyncClient, not {type(http_client).__name__}"
                + (" (use PdfConversionApi)" if isinstance(http_client, httpx.Client) else "")
            )
        self._owns_http = http_client is None
        self._http = http_client if http_client is not None else httpx.AsyncClient()
        self._sleep = sleep if sleep is not None else _default_sleep

    async def aclose(self) -> None:
        """Closes the connections, unless the httpx.AsyncClient was passed in."""
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> AsyncPdfConversionApi:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    # The API

    async def convert(
        self,
        from_format: str,
        to_format: str,
        files: FileInput | Sequence[FileInput] | None = None,
        params: Mapping[str, ParamValue] | None = None,
        *,
        file_name: str | None = None,
        store_file: bool = False,
        timeout: float | None = None,
    ) -> AsyncConversionResult:
        """Converts files: ``POST /v2/convert/{from_format}/to/{to_format}``.

        The arguments are those of :meth:`PdfConversionApi.convert`.
        """
        call = self._convert_call(
            from_format, to_format, files, params, file_name, store_file, timeout
        )
        return self._result(await self._json(call, parse_conversion))

    async def convert_file(
        self,
        path: str | os.PathLike[str],
        to_format: str,
        params: Mapping[str, ParamValue] | None = None,
        *,
        file_name: str | None = None,
        store_file: bool = False,
        timeout: float | None = None,
    ) -> AsyncConversionResult:
        """Converts one file on disk; its extension is the input format."""
        from_format, upload = self._file_input(path)
        return await self.convert(
            from_format,
            to_format,
            upload,
            params,
            file_name=file_name,
            store_file=store_file,
            timeout=timeout,
        )

    async def usage(self, *, timeout: float | None = None) -> Usage:
        """Your plan, its limits and this month's count: ``GET /v2/usage``."""
        return await self._json(self._usage_call(timeout), parse_usage)

    async def conversions(self, *, timeout: float | None = None) -> list[Conversion]:
        """Every conversion the API offers: ``GET /v2/info``. Needs no key."""
        return await self._json(self._conversions_call(timeout), parse_conversions)

    async def download(self, url: str, *, timeout: float | None = None) -> bytes:
        """Downloads a stored file's ``url`` into memory. The key is not sent."""
        call = self._download_call(url, timeout)

        async def handle(response: httpx.Response, deadline: Deadline) -> bytes:
            body = await self._read(response, call, deadline)
            if response.status_code != 200:
                raise api_error(response.status_code, response.headers, body)
            return bytes(body)

        return await self._execute(call, handle)

    async def download_to(
        self, url: str, path: str | os.PathLike[str], *, timeout: float | None = None
    ) -> Path:
        """Downloads a stored file's ``url`` to ``path``, streamed to disk, and returns the path.

        If ``path`` is an existing folder, the file is written inside it under the name at the
        end of the URL. An existing file is overwritten. The key is not sent.
        """
        call = self._download_call(url, timeout)
        target = Path(path)
        if target.is_dir():
            target = target / safe_file_name(url_file_name(call.url))

        async def handle(response: httpx.Response, deadline: Deadline) -> Path:
            if response.status_code != 200:
                body = await self._read(response, call, deadline)
                raise api_error(response.status_code, response.headers, body)
            try:
                with open(target, "wb") as out:
                    async for chunk in response.aiter_bytes():
                        out.write(chunk)
                        deadline.check()
            except httpx.HTTPError as exc:
                discard_partial(target)
                raise body_error(exc, call, self._settings) from exc
            except BaseException:
                discard_partial(target)
                raise
            return target

        return await self._execute(call, handle)

    async def delete_stored_file(self, file_id: str, *, timeout: float | None = None) -> bool:
        """Deletes a stored file now: ``DELETE /v2/d/{file_id}``.

        Returns True when it was deleted, False when there was nothing to delete.
        """
        call = self._delete_call(file_id, timeout)

        async def handle(response: httpx.Response, deadline: Deadline) -> bool:
            if 200 <= response.status_code < 300:
                return True
            body = await self._read(response, call, deadline)
            if response.status_code == 404:
                return False
            raise api_error(response.status_code, response.headers, body)

        return await self._execute(call, handle)

    # Plumbing

    def _result(self, parsed: ParsedConversion) -> AsyncConversionResult:
        files = [AsyncOutputFile(entry, self) for entry in parsed.files]
        return AsyncConversionResult(
            files, parsed.conversion_cost, parsed.conversion_time, parsed.request_id
        )

    async def _json(self, call: Call, parse: Callable[[bytearray, str | None], T]) -> T:
        async def handle(response: httpx.Response, deadline: Deadline) -> T:
            body = await self._read(response, call, deadline)
            if response.status_code != 200:
                raise api_error(response.status_code, response.headers, body)
            return parse(body, request_id(response.headers))

        return await self._execute(call, handle)

    async def _read(self, response: httpx.Response, call: Call, deadline: Deadline) -> bytearray:
        body = bytearray()
        try:
            async for chunk in response.aiter_bytes():
                body += chunk
                deadline.check()
        except httpx.HTTPError as exc:
            raise body_error(exc, call, self._settings) from exc
        return body

    def _build(self, call: Call, deadline: Deadline) -> httpx.Request:
        body = MultipartBody(call.fields) if call.fields is not None else None
        request = self._http.build_request(
            call.method,
            call.url,
            headers=request_headers(call, self._settings, body),
            content=body.achunks(deadline.check) if body is not None else None,
            timeout=httpx_timeout(call, self._settings),
        )
        if not call.authorized:
            request.headers.pop("Authorization", None)
        return request

    async def _execute(
        self, call: Call, handle: Callable[[httpx.Response, Deadline], Awaitable[T]]
    ) -> T:
        """Sends ``call`` with the retry rules, then hands the final response to ``handle``."""
        state = RetryState(self._settings.max_retries, self._settings.max_retry_wait)
        while True:
            state.next_attempt()
            deadline = Deadline(call)
            request = self._build(call, deadline)
            try:
                response = await self._http.send(request, stream=True, follow_redirects=False)
            except httpx.TransportError as exc:
                wait = wait_after_send_error(exc, state, call, self._settings)
                why = f"no response ({type(exc).__name__})"
            else:
                try:
                    retry = state.after_status(
                        response.status_code, response.headers.get("retry-after")
                    )
                    if retry is None:
                        return await handle(response, deadline)
                    wait, why = retry, f"HTTP {response.status_code}"
                finally:
                    await response.aclose()
            log_retry(call, state, wait, why)
            await self._sleep(wait)
