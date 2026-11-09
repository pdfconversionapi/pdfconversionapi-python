"""What the sync and async clients share: settings, request building, retry decisions,
response parsing and error mapping. Nothing here does I/O on the network."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import math
import os
import platform
import random
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple, Union

import httpx

from ._exceptions import (
    ERRORS_BY_STATUS,
    APIConnectionError,
    ApiError,
    APITimeoutError,
    InvalidResponseError,
    MissingApiKeyError,
)
from ._inputs import FileInput, UploadFile, own_name, to_upload, to_uploads
from ._models import Conversion, Usage
from ._multipart import FieldValue, MultipartBody
from ._version import __version__

logger = logging.getLogger("pdfconversionapi")

DEFAULT_BASE_URL = "https://api.pdfconversionapi.com"
DEFAULT_TIMEOUT = 240.0
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_MAX_RETRIES = 4
DEFAULT_MAX_RETRY_WAIT = 60.0

KEY_ENV = "PDFCONVERSIONAPI_KEY"
BASE_URL_ENV = "PDFCONVERSIONAPI_BASE_URL"

USER_AGENT = f"pdfconversionapi-python/{__version__} python/{platform.python_version()}"

_FORMAT = re.compile(r"[A-Za-z0-9-]{1,20}")
_FILE_ID = re.compile(r"[0-9a-fA-F]{32}")
_KEY_CHARS = re.compile(r"[\x21-\x7e]+")
_DIGITS = re.compile(r"[0-9]+")

Number = Union[int, float, Decimal]
ParamValue = Union[str, int, float, Decimal, bool, None, FileInput]
"""A parameter value: text, a number, a boolean, ``None`` (left out), or a file sent as a named
file field (a ``pathlib.Path``, bytes, a binary stream or an :class:`UploadFile`). A ``str``
value is always text, never a path."""


# Settings


@dataclass(frozen=True)
class Settings:
    api_key: str | None = field(repr=False)
    base_url: str
    timeout: float
    connect_timeout: float
    max_retries: int
    max_retry_wait: float


def _seconds(value: object, name: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number of seconds, not {type(value).__name__}")
    if math.isnan(value) or value < 0 or (value == 0 and not allow_zero) or math.isinf(value):
        raise ValueError(f"{name} must be a positive number of seconds, got {value!r}")
    return float(value)


def resolve_settings(
    *,
    api_key: str | None,
    base_url: str | None,
    timeout: float,
    connect_timeout: float,
    max_retries: int,
    max_retry_wait: float,
) -> Settings:
    if api_key is None:
        api_key = os.environ.get(KEY_ENV)
    elif not isinstance(api_key, str):
        raise TypeError(f"api_key must be a str, not {type(api_key).__name__}")
    key = (api_key or "").strip() or None
    if key is not None and not _KEY_CHARS.fullmatch(key):
        # Never echo the key: say only what is wrong with it.
        raise ValueError("api_key has characters that can't be sent in an HTTP header")

    if base_url is None:
        base_url = os.environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL
    elif not isinstance(base_url, str):
        raise TypeError(f"base_url must be a str, not {type(base_url).__name__}")
    base = base_url.strip().rstrip("/")
    try:
        parsed = httpx.URL(base)
    except (httpx.InvalidURL, TypeError, ValueError):
        parsed = None
    if (
        parsed is None
        or parsed.scheme not in ("http", "https")
        or not parsed.host
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            f"base_url must be an http or https URL without a query, such as "
            f"{DEFAULT_BASE_URL!r}; got {base_url!r}"
        )

    if isinstance(max_retries, bool) or not isinstance(max_retries, int):
        raise TypeError(f"max_retries must be an int, not {type(max_retries).__name__}")
    if max_retries < 0:
        raise ValueError(f"max_retries must be 0 or more, got {max_retries}")

    return Settings(
        api_key=key,
        base_url=base,
        timeout=_seconds(timeout, "timeout"),
        connect_timeout=_seconds(connect_timeout, "connect_timeout"),
        max_retries=max_retries,
        max_retry_wait=_seconds(max_retry_wait, "max_retry_wait", allow_zero=True),
    )


def scrub(text: str, settings: Settings) -> str:
    """Removes the key from a message, in case a lower layer ever quoted it."""
    return text.replace(settings.api_key, "***") if settings.api_key else text


# Requests


@dataclass(frozen=True)
class Call:
    """One API call, before any attempt."""

    method: str
    url: str
    timeout: float
    authorized: bool = False
    accept_json: bool = True
    fields: Sequence[tuple[str, FieldValue]] | None = None
    conversion: bool = False
    label: str = ""


def check_format(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{what} must be a str, not {type(value).__name__}")
    if not _FORMAT.fullmatch(value):
        raise ValueError(f"{what} must be 1 to 20 letters, digits or hyphens; got {value!r}")
    return value.lower()


def check_file_id(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"file_id must be a str, not {type(value).__name__}")
    if not _FILE_ID.fullmatch(value):
        raise ValueError(
            f"file_id must be 32 hex characters, as in a stored file's FileId; got {value!r}"
        )
    return value.lower()


def check_url(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"url must be a str, not {type(value).__name__}")
    try:
        parsed = httpx.URL(value)
    except (httpx.InvalidURL, TypeError, ValueError):
        parsed = None
    if parsed is None or parsed.scheme not in ("http", "https") or not parsed.host:
        raise ValueError("url must be an absolute http or https URL, such as a stored file's url")
    return value


def call_timeout(settings: Settings, timeout: float | None) -> float:
    return settings.timeout if timeout is None else _seconds(timeout, "timeout")


def require_key(settings: Settings) -> None:
    if settings.api_key is None:
        raise MissingApiKeyError(
            "this call needs an API key: pass api_key= to the client or set the "
            f"{KEY_ENV} environment variable (get a key at https://app.pdfconversionapi.com)"
        )


def format_number(value: Number) -> str:
    """Plain decimal: 72, 1.5, 0.0001 (never 1e-04)."""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"parameter values must be finite numbers, got {value!r}")
        if value.is_integer():
            return str(int(value))
        value = Decimal(repr(value))
    if not value.is_finite():
        raise ValueError(f"parameter values must be finite numbers, got {value!r}")
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def encode_param(name: str, value: object) -> FieldValue | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, Decimal)):
        return format_number(value)
    return to_upload(value, f"parameter {name!r}")


def convert_fields(
    files: object,
    params: Mapping[str, object] | None,
    file_name: str | None,
    store_file: bool,
) -> list[tuple[str, FieldValue]]:
    """The form fields in order: the input files, the parameters, FileName, StoreFile."""
    uploads = to_uploads(files)
    if params is not None and not isinstance(params, Mapping):
        raise TypeError(f"params must be a mapping of names to values, not {type(params).__name__}")
    if file_name is not None and not isinstance(file_name, str):
        raise TypeError(f"file_name must be a str, not {type(file_name).__name__}")
    if not isinstance(store_file, bool):
        raise TypeError(f"store_file must be a bool, not {type(store_file).__name__}")

    file_field = "File" if len(uploads) == 1 else "Files"
    fields: list[tuple[str, FieldValue]] = [(file_field, upload) for upload in uploads]
    overridden = set()
    if file_name is not None:
        overridden.add("filename")
    if store_file:
        overridden.add("storefile")
    for name, value in (params or {}).items():
        if not isinstance(name, str):
            raise TypeError(f"parameter names must be str, not {type(name).__name__}")
        if name.lower() in overridden:
            continue  # the typed option wins
        encoded = encode_param(name, value)
        if encoded is not None:
            fields.append((name, encoded))
    if file_name is not None:
        fields.append(("FileName", file_name))
    if store_file:
        fields.append(("StoreFile", "true"))
    return fields


def request_headers(call: Call, settings: Settings, body: MultipartBody | None) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    if call.accept_json:
        headers["Accept"] = "application/json"
    if call.authorized and settings.api_key is not None:
        headers["Authorization"] = f"Bearer {settings.api_key}"
    if body is not None:
        headers["Content-Type"] = body.content_type
        headers["Content-Length"] = str(body.content_length)
    return headers


def httpx_timeout(call: Call, settings: Settings) -> httpx.Timeout:
    return httpx.Timeout(call.timeout, connect=min(settings.connect_timeout, call.timeout))


class Deadline:
    """The end of one attempt's time. ``check()`` raises APITimeoutError once it has passed."""

    def __init__(self, call: Call) -> None:
        self._call = call
        self._at = time.monotonic() + call.timeout

    def check(self) -> None:
        if time.monotonic() > self._at:
            raise timeout_error(self._call)


def timeout_error(call: Call) -> APITimeoutError:
    text = (
        f"{call.label}: no complete response within {call.timeout:g} seconds (the client's timeout)"
    )
    if call.conversion:
        text += (
            ". The conversion may still be running on the server, and it counts toward this "
            "month's conversions if it succeeds; give it more time with timeout= rather than "
            "retrying at once"
        )
    return APITimeoutError(text)


def _reason(exc: BaseException, settings: Settings) -> str:
    return scrub(str(exc) or type(exc).__name__, settings)


def connection_error(
    exc: BaseException, call: Call, attempts: int, settings: Settings
) -> APIConnectionError:
    host = httpx.URL(call.url).host
    tries = "1 attempt" if attempts == 1 else f"{attempts} attempts"
    return APIConnectionError(
        f"{call.label}: no response from {host} after {tries}: {_reason(exc, settings)}"
    )


def body_error(exc: httpx.HTTPError, call: Call, settings: Settings) -> Exception:
    """An error while the body of a response was read: never retried."""
    if isinstance(exc, httpx.TimeoutException):
        return timeout_error(call)
    if isinstance(exc, httpx.DecodingError):
        return InvalidResponseError(f"{call.label}: the response body could not be decoded: {exc}")
    return APIConnectionError(
        f"{call.label}: the connection was closed while the response was being read "
        f"({_reason(exc, settings)}). Not retried: a conversion may already have run and counted"
    )


# Retries


def jitter(attempt: int, rand: Callable[[], float] = random.random) -> float:
    """A random number of seconds in [0, 2**attempt)."""
    return float(rand() * (2**attempt))


def parse_retry_after(value: str | None) -> int | None:
    """Whole seconds, or None when the header is missing or unreadable."""
    if value is None:
        return None
    text = value.strip()
    return int(text) if _DIGITS.fullmatch(text) else None


class RetryState:
    """The retry decisions for one call (SPEC section 8). One per call, never shared."""

    def __init__(
        self, max_retries: int, max_retry_wait: float, rand: Callable[[], float] = random.random
    ) -> None:
        self.attempt = 0
        self._max_attempts = max_retries + 1
        self._max_retry_wait = max_retry_wait
        self._rand = rand
        self._retried_500 = False

    def next_attempt(self) -> None:
        self.attempt += 1

    def _attempts_left(self) -> bool:
        return self.attempt < self._max_attempts

    def after_no_response(self) -> float | None:
        """Seconds to wait before retrying, or None to raise."""
        if not self._attempts_left():
            return None
        return jitter(self.attempt, self._rand)

    def after_status(self, status: int, retry_after: str | None) -> float | None:
        """Seconds to wait before retrying, or None to return or raise this response."""
        if status in (429, 503):
            wait = parse_retry_after(retry_after)
            wait = 1 if wait is None else wait
            if self._attempts_left() and wait <= self._max_retry_wait:
                return wait + jitter(self.attempt, self._rand)
            return None
        if status == 500 and not self._retried_500 and self._attempts_left():
            self._retried_500 = True
            return 1 + jitter(self.attempt, self._rand)
        return None


def wait_after_send_error(
    exc: httpx.TransportError, state: RetryState, call: Call, settings: Settings
) -> float:
    """What to do when no status line arrived: the seconds to wait, or raise."""
    if isinstance(exc, httpx.TimeoutException) and not isinstance(exc, httpx.ConnectTimeout):
        raise timeout_error(call) from exc
    if isinstance(exc, (httpx.UnsupportedProtocol, httpx.LocalProtocolError)):
        raise connection_error(exc, call, state.attempt, settings) from exc
    wait = state.after_no_response()
    if wait is None:
        raise connection_error(exc, call, state.attempt, settings) from exc
    return wait


def log_retry(call: Call, state: RetryState, wait: float, why: str) -> None:
    logger.info("%s: %s; retrying in %.2f s (attempt %d)", call.label, why, wait, state.attempt + 1)


# Responses


def request_id(headers: Mapping[str, str]) -> str | None:
    return headers.get("x-request-id") or None


def api_error(status: int, headers: Mapping[str, str], body: bytes | bytearray) -> ApiError:
    """The error for a non-success response (SPEC section 9)."""
    text = body.decode("utf-8", "replace")
    content_type = headers.get("content-type")
    mime = content_type.split(";", 1)[0].strip() if content_type else ""
    message: str | None = None
    if not text.strip():
        message = "empty response"
    else:
        try:
            parsed = json.loads(body)
        except (ValueError, RecursionError):
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("Message"), str):
            message = parsed["Message"]
        elif mime.lower() == "text/plain" and len(text) <= 1000:
            message = text.strip()
        else:
            message = f"non-JSON response ({mime or 'no content type'})"
    error_class = ERRORS_BY_STATUS.get(status, ApiError)
    return error_class(
        status,
        message,
        request_id=request_id(headers),
        retry_after=parse_retry_after(headers.get("retry-after")),
        body=text,
    )


def _invalid(what: str, rid: str | None) -> InvalidResponseError:
    return InvalidResponseError(f"unusable response from the API: {what}", request_id=rid)


def parse_json(body: bytes | bytearray, rid: str | None) -> Any:
    try:
        return json.loads(body)
    except (ValueError, RecursionError):
        snippet = bytes(body[:80]).decode("utf-8", "replace")
        raise _invalid(f"the body is not JSON (it starts {snippet!r})", rid) from None


def _field(obj: Mapping[str, Any], key: str, kind: type | tuple[type, ...], rid: str | None) -> Any:
    value = obj.get(key)
    if isinstance(value, bool) and kind is not bool:
        raise _invalid(f"{key} is {value!r}", rid)
    if not isinstance(value, kind):
        raise _invalid(f"{key} is missing or not a {getattr(kind, '__name__', kind)}", rid)
    return value


@dataclass(frozen=True)
class FileEntry:
    """One entry of ``Files``, checked and decoded."""

    name: str
    ext: str
    size: int
    data: bytes | None
    url: str | None
    file_id: str | None


class ParsedConversion(NamedTuple):
    conversion_cost: int
    conversion_time: float
    files: list[FileEntry]
    request_id: str | None


def parse_conversion(body: bytes | bytearray, rid: str | None) -> ParsedConversion:
    doc = parse_json(body, rid)
    if not isinstance(doc, dict):
        raise _invalid("the body is not a JSON object", rid)
    entries = doc.get("Files")
    if not isinstance(entries, list) or not entries:
        raise _invalid("Files is missing or empty", rid)
    cost = doc.get("ConversionCost", 0)
    if isinstance(cost, bool) or not isinstance(cost, int):
        raise _invalid(f"ConversionCost is {cost!r}", rid)
    seconds = doc.get("ConversionTime", 0)
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise _invalid(f"ConversionTime is {seconds!r}", rid)

    files = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise _invalid("an entry of Files is not an object", rid)
        name = _field(entry, "FileName", str, rid)
        size = _field(entry, "FileSize", int, rid)
        ext = entry.get("FileExt")
        if not isinstance(ext, str):
            ext = Path(own_name(name)).suffix.lstrip(".").lower()
        encoded = entry.get("FileData")
        if isinstance(encoded, str):
            try:
                data = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                raise _invalid(f"FileData of {name!r} is not valid base64", rid) from None
            if len(data) != size:
                raise _invalid(
                    f"{name!r} decodes to {len(data)} bytes, but FileSize says {size}", rid
                )
            files.append(FileEntry(name, ext, size, data, None, None))
        else:
            url = _field(entry, "Url", str, rid)
            file_id = _field(entry, "FileId", str, rid)
            files.append(FileEntry(name, ext, size, None, url, file_id))
    return ParsedConversion(cost, float(seconds), files, rid)


def _datetime(value: Any, key: str, rid: str | None) -> datetime:
    if not isinstance(value, str):
        raise _invalid(f"{key} is missing or not a string", rid)
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise _invalid(f"{key} is not a date-time: {value!r}", rid) from None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_usage(body: bytes | bytearray, rid: str | None) -> Usage:
    doc = parse_json(body, rid)
    if not isinstance(doc, dict):
        raise _invalid("the body is not a JSON object", rid)
    remaining = doc.get("Remaining")
    if remaining is not None:
        remaining = _field(doc, "Remaining", int, rid)
    max_upload = doc.get("MaxUploadMB")
    if isinstance(max_upload, float) and max_upload.is_integer():
        max_upload = int(max_upload)
    else:
        max_upload = _field(doc, "MaxUploadMB", int, rid)
    return Usage(
        plan=_field(doc, "Plan", str, rid),
        included=_field(doc, "Included", int, rid),
        used=_field(doc, "Used", int, rid),
        remaining=remaining,
        overage=_field(doc, "Overage", bool, rid),
        running=_field(doc, "Running", int, rid),
        concurrent_limit=_field(doc, "ConcurrentLimit", int, rid),
        max_upload_mb=max_upload,
        period_start=_datetime(doc.get("PeriodStart"), "PeriodStart", rid),
        period_end=_datetime(doc.get("PeriodEnd"), "PeriodEnd", rid),
    )


def parse_conversions(body: bytes | bytearray, rid: str | None) -> list[Conversion]:
    doc = parse_json(body, rid)
    if not isinstance(doc, list):
        raise _invalid("the body is not a JSON array", rid)
    conversions = []
    for entry in doc:
        if not isinstance(entry, dict):
            raise _invalid("an entry is not an object", rid)
        conversions.append(
            Conversion(
                from_format=_field(entry, "From", str, rid),
                to_format=_field(entry, "To", str, rid),
                title=_field(entry, "Title", str, rid),
                category=_field(entry, "Category", str, rid),
                inputs=_field(entry, "Inputs", str, rid),
            )
        )
    return conversions


__all__ = [
    "DEFAULT_BASE_URL",
    "Call",
    "Deadline",
    "FileEntry",
    "ParamValue",
    "RetryState",
    "Settings",
    "UploadFile",
]
