"""The synchronous client."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping, Sequence
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
from ._models import Conversion, ConversionResult, OutputFile, Usage
from ._multipart import MultipartBody

__all__ = ["PdfConversionApi"]

T = TypeVar("T")


class PdfConversionApi(ClientBase):
    """The PDF Conversion API client.

    >>> client = PdfConversionApi()                  # reads PDFCONVERSIONAPI_KEY
    >>> result = client.convert_file("report.docx", "pdf")
    >>> result.save_all("out")                       # writes out/report.pdf

    Args:
        api_key: the API key. Defaults to the ``PDFCONVERSIONAPI_KEY`` environment variable.
            It may be missing: ``conversions()`` and downloads need none, and other calls raise
            MissingApiKeyError before sending anything.
        base_url: defaults to ``PDFCONVERSIONAPI_BASE_URL``, else
            ``https://api.pdfconversionapi.com``.
        timeout: seconds each attempt may take, for the whole response (default 240).
        connect_timeout: seconds to wait for a connection to open (default 10).
        max_retries: retries after the first attempt (default 4); 0 turns retries off.
        max_retry_wait: the longest ``Retry-After`` to wait for (default 60 seconds). A longer
            one, such as a used-up month, is raised at once.
        http_client: your own ``httpx.Client`` (proxies, custom TLS). It is not closed by
            :meth:`close`. The timeout is set on each request.
        sleep: replaces ``time.sleep`` between retries. For tests.

    The client is safe to share between threads. Close it with :meth:`close`, or use it as a
    context manager.
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
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            connect_timeout=connect_timeout,
            max_retries=max_retries,
            max_retry_wait=max_retry_wait,
        )
        if http_client is not None and not isinstance(http_client, httpx.Client):
            raise TypeError(
                f"http_client must be an httpx.Client, not {type(http_client).__name__}"
                + (
                    " (use AsyncPdfConversionApi)"
                    if isinstance(http_client, httpx.AsyncClient)
                    else ""
                )
            )
        self._owns_http = http_client is None
        self._http = http_client if http_client is not None else httpx.Client()
        self._sleep = sleep if sleep is not None else time.sleep

    def close(self) -> None:
        """Closes the connections, unless the httpx.Client was passed in."""
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> PdfConversionApi:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # The API

    def convert(
        self,
        from_format: str,
        to_format: str,
        files: FileInput | Sequence[FileInput] | None = None,
        params: Mapping[str, ParamValue] | None = None,
        *,
        file_name: str | None = None,
        store_file: bool = False,
        timeout: float | None = None,
    ) -> ConversionResult:
        """Converts files: ``POST /v2/convert/{from_format}/to/{to_format}``.

        Args:
            from_format: the input format, such as ``docx``, or ``pdf`` for the PDF tools.
            to_format: the output format, such as ``pdf``, or a tool such as ``merge``.
            files: one input or a list of inputs. An input is a path (a ``str`` is always a
                path), bytes, a binary stream or an :class:`UploadFile`. One file is sent as
                ``File``, several as repeated ``Files``. Leave it out for conversions that take
                no file, such as ``data/to/qrcode``.
            params: the conversion's parameters, such as ``{"PageRange": "1-3"}``. Numbers and
                booleans are sent as text, ``None`` is left out, and a file value (a
                ``pathlib.Path``, bytes, a stream or an UploadFile) is sent as a named file field
                such as ``ImageFile``.
            file_name: the output's name, without the extension (sent as ``FileName``).
            store_file: return download links instead of data (sent as ``StoreFile=true``).
            timeout: seconds for this call's attempts, instead of the client's.
        """
        call = self._convert_call(
            from_format, to_format, files, params, file_name, store_file, timeout
        )
        return self._result(self._json(call, parse_conversion))

    def convert_file(
        self,
        path: str | os.PathLike[str],
        to_format: str,
        params: Mapping[str, ParamValue] | None = None,
        *,
        file_name: str | None = None,
        store_file: bool = False,
        timeout: float | None = None,
    ) -> ConversionResult:
        """Converts one file on disk; its extension is the input format.

        ``convert_file("report.docx", "pdf")`` is ``convert("docx", "pdf", "report.docx")``.
        Raises ValueError when the path has no extension.
        """
        from_format, upload = self._file_input(path)
        return self.convert(
            from_format,
            to_format,
            upload,
            params,
            file_name=file_name,
            store_file=store_file,
            timeout=timeout,
        )

    def usage(self, *, timeout: float | None = None) -> Usage:
        """Your plan, its limits and this month's count: ``GET /v2/usage``."""
        return self._json(self._usage_call(timeout), parse_usage)

    def conversions(self, *, timeout: float | None = None) -> list[Conversion]:
        """Every conversion the API offers: ``GET /v2/info``. Needs no key."""
        return self._json(self._conversions_call(timeout), parse_conversions)

    def download(self, url: str, *, timeout: float | None = None) -> bytes:
        """Downloads a stored file's ``url`` into memory. The key is not sent."""
        call = self._download_call(url, timeout)

        def handle(response: httpx.Response, deadline: Deadline) -> bytes:
            body = self._read(response, call, deadline)
            if response.status_code != 200:
                raise api_error(response.status_code, response.headers, body)
            return bytes(body)

        return self._execute(call, handle)

    def download_to(
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

        def handle(response: httpx.Response, deadline: Deadline) -> Path:
            if response.status_code != 200:
                body = self._read(response, call, deadline)
                raise api_error(response.status_code, response.headers, body)
            try:
                with open(target, "wb") as out:
                    for chunk in response.iter_bytes():
                        out.write(chunk)
                        deadline.check()
            except httpx.HTTPError as exc:
                discard_partial(target)
                raise body_error(exc, call, self._settings) from exc
            except BaseException:
                discard_partial(target)
                raise
            return target

        return self._execute(call, handle)

    def delete_stored_file(self, file_id: str, *, timeout: float | None = None) -> bool:
        """Deletes a stored file now: ``DELETE /v2/d/{file_id}``.

        Returns True when it was deleted, False when there was nothing to delete (unknown id,
        already deleted or expired, or another account's file).
        """
        call = self._delete_call(file_id, timeout)

        def handle(response: httpx.Response, deadline: Deadline) -> bool:
            if 200 <= response.status_code < 300:
                return True
            body = self._read(response, call, deadline)
            if response.status_code == 404:
                return False
            raise api_error(response.status_code, response.headers, body)

        return self._execute(call, handle)

    # Plumbing

    def _result(self, parsed: ParsedConversion) -> ConversionResult:
        files = [OutputFile(entry, self) for entry in parsed.files]
        return ConversionResult(
            files, parsed.conversion_cost, parsed.conversion_time, parsed.request_id
        )

    def _json(self, call: Call, parse: Callable[[bytearray, str | None], T]) -> T:
        def handle(response: httpx.Response, deadline: Deadline) -> T:
            body = self._read(response, call, deadline)
            if response.status_code != 200:
                raise api_error(response.status_code, response.headers, body)
            return parse(body, request_id(response.headers))

        return self._execute(call, handle)

    def _read(self, response: httpx.Response, call: Call, deadline: Deadline) -> bytearray:
        body = bytearray()
        try:
            for chunk in response.iter_bytes():
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
            content=body.chunks(deadline.check) if body is not None else None,
            timeout=httpx_timeout(call, self._settings),
        )
        if not call.authorized:
            request.headers.pop("Authorization", None)
        return request

    def _execute(self, call: Call, handle: Callable[[httpx.Response, Deadline], T]) -> T:
        """Sends ``call`` with the retry rules, then hands the final response to ``handle``."""
        state = RetryState(self._settings.max_retries, self._settings.max_retry_wait)
        while True:
            state.next_attempt()
            deadline = Deadline(call)
            request = self._build(call, deadline)
            try:
                response = self._http.send(request, stream=True, follow_redirects=False)
            except httpx.TransportError as exc:
                wait = wait_after_send_error(exc, state, call, self._settings)
                why = f"no response ({type(exc).__name__})"
            else:
                try:
                    retry = state.after_status(
                        response.status_code, response.headers.get("retry-after")
                    )
                    if retry is None:
                        return handle(response, deadline)
                    wait, why = retry, f"HTTP {response.status_code}"
                finally:
                    response.close()
            log_retry(call, state, wait, why)
            self._sleep(wait)
