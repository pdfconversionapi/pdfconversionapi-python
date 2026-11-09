"""Response parsing and error mapping."""

from __future__ import annotations

import base64
import json
import pickle
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from pdfconversionapi import (
    ApiError,
    AuthenticationError,
    BadRequestError,
    GatewayTimeoutError,
    InternalServerError,
    InvalidResponseError,
    NotFoundError,
    PayloadTooLargeError,
    PermissionDeniedError,
    RateLimitError,
    ServiceUnavailableError,
    UnprocessableEntityError,
)
from pdfconversionapi._core import (
    api_error,
    parse_conversion,
    parse_conversions,
    parse_retry_after,
    parse_usage,
)


def headers(**values: str) -> httpx.Headers:
    return httpx.Headers({k.replace("_", "-"): v for k, v in values.items()})


# Error mapping


@pytest.mark.parametrize(
    ("status", "cls"),
    [
        (400, BadRequestError),
        (401, AuthenticationError),
        (403, PermissionDeniedError),
        (404, NotFoundError),
        (413, PayloadTooLargeError),
        (422, UnprocessableEntityError),
        (429, RateLimitError),
        (500, InternalServerError),
        (503, ServiceUnavailableError),
        (504, GatewayTimeoutError),
    ],
)
def test_statuses_map_to_classes(status: int, cls: type[ApiError]) -> None:
    error = api_error(status, headers(content_type="application/json"), b'{"Code":1,"Message":"m"}')
    assert type(error) is cls
    assert isinstance(error, ApiError)
    assert error.status == status


def test_other_statuses_are_plain_api_errors() -> None:
    error = api_error(502, headers(content_type="text/html"), b"<html>bad gateway</html>")
    assert type(error) is ApiError
    assert error.message == "non-JSON response (text/html)"
    assert error.request_id is None
    assert str(error) == "HTTP 502: non-JSON response (text/html)"


def test_json_message_is_kept_unchanged() -> None:
    body = json.dumps({"Code": 422, "Message": "  the input file is password-protected  "})
    error = api_error(
        422, headers(content_type="application/json", x_request_id="rid-1"), body.encode()
    )
    assert error.message == "  the input file is password-protected  "
    assert error.request_id == "rid-1"
    assert error.body == body
    assert str(error) == "HTTP 422:   the input file is password-protected   (request id: rid-1)"


@pytest.mark.parametrize("body", [b"", b"  \r\n"])
def test_empty_body(body: bytes) -> None:
    error = api_error(504, headers(content_type="application/json", x_request_id="r"), body)
    assert error.message == "empty response"


def test_short_plain_text_is_the_message() -> None:
    error = api_error(
        400, headers(content_type="text/plain; charset=utf-8"), b"  Invalid `boundary`\n"
    )
    assert error.message == "Invalid `boundary`"


def test_long_plain_text_is_not() -> None:
    error = api_error(400, headers(content_type="text/plain"), b"x" * 1001)
    assert error.message == "non-JSON response (text/plain)"
    assert len(error.body) == 1001
    assert api_error(400, headers(content_type="text/plain"), b"x" * 1000).message == "x" * 1000


@pytest.mark.parametrize(
    ("content_type", "body", "message"),
    [
        (None, b"<html/>", "non-JSON response (no content type)"),
        ("application/json", b'{"Code": 500}', "non-JSON response (application/json)"),
        ("application/json", b'{"Message": 5}', "non-JSON response (application/json)"),
        ("application/json", b"[1, 2]", "non-JSON response (application/json)"),
        ("Text/HTML; charset=utf-8", b"<html/>", "non-JSON response (Text/HTML)"),
        ("text/plain", b'{"Message": "json wins"}', "json wins"),
        ("application/json", b"\xff\xfe", "non-JSON response (application/json)"),
    ],
)
def test_other_bodies(content_type: str | None, body: bytes, message: str) -> None:
    values = {"content-type": content_type} if content_type else {}
    assert api_error(500, httpx.Headers(values), body).message == message


def test_retry_after() -> None:
    error = api_error(
        429,
        headers(content_type="application/json", retry_after="2000000"),
        b'{"Code":429,"Message":"used up"}',
    )
    assert error.retry_after == 2_000_000
    assert api_error(503, headers(), b"").retry_after is None


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("5", 5),
        (" 10 ", 10),
        ("0", 0),
        (None, None),
        ("", None),
        ("1.5", None),
        ("-1", None),
        ("Wed, 21 Oct 2026 07:28:00 GMT", None),
        ("²", None),
    ],
)
def test_parse_retry_after(value: str | None, seconds: int | None) -> None:
    assert parse_retry_after(value) == seconds


def test_errors_survive_pickling() -> None:
    error = api_error(
        429, headers(x_request_id="rid", retry_after="5"), b'{"Code":429,"Message":"slow down"}'
    )
    copy = pickle.loads(pickle.dumps(error))
    assert type(copy) is RateLimitError
    assert (copy.status, copy.message, copy.request_id, copy.retry_after, copy.body) == (
        429,
        "slow down",
        "rid",
        5,
        error.body,
    )
    assert str(copy) == str(error)
    invalid = pickle.loads(pickle.dumps(InvalidResponseError("bad", request_id="r")))
    assert str(invalid) == "bad (request id: r)"


# Conversion results


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def conversion_body(*files: dict[str, Any], **extra: Any) -> bytes:
    doc = {"ConversionCost": 1, "ConversionTime": 0.036, "Files": list(files), **extra}
    return json.dumps(doc).encode()


def test_parse_inline_and_stored_files() -> None:
    parsed = parse_conversion(
        conversion_body(
            {"FileName": "a-1.jpg", "FileExt": "jpg", "FileSize": 3, "FileData": b64(b"abc")},
            {
                "FileName": "a-2.jpg",
                "FileExt": "jpg",
                "FileSize": 9,
                "Url": "https://api.example/v2/d/" + "0" * 32 + "/a-2.jpg",
                "FileId": "0" * 32,
            },
        ),
        "rid",
    )
    assert parsed.conversion_cost == 1
    assert parsed.conversion_time == 0.036
    assert parsed.request_id == "rid"
    first, second = parsed.files
    assert (first.name, first.ext, first.size, first.data, first.url) == (
        "a-1.jpg",
        "jpg",
        3,
        b"abc",
        None,
    )
    assert second.data is None
    assert second.file_id == "0" * 32


def test_missing_file_ext_comes_from_the_name() -> None:
    parsed = parse_conversion(
        conversion_body({"FileName": "x.PDF", "FileSize": 1, "FileData": b64(b"x")}), None
    )
    assert parsed.files[0].ext == "pdf"


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        (b"{not json", "not JSON"),
        (b"[]", "not a JSON object"),
        (b"{}", "Files is missing"),
        (conversion_body(), "Files is missing or empty"),
        (b'{"Files": [1]}', "not an object"),
        (conversion_body({"FileSize": 1, "FileData": "eA=="}), "FileName"),
        (conversion_body({"FileName": "a", "FileData": "eA=="}), "FileSize"),
        (conversion_body({"FileName": "a", "FileSize": True, "FileData": "eA=="}), "FileSize"),
        (conversion_body({"FileName": "a", "FileSize": 2, "FileData": "eA=="}), "decodes to 1"),
        (conversion_body({"FileName": "a", "FileSize": 1, "FileData": "eA"}), "not valid base64"),
        (
            conversion_body({"FileName": "a", "FileSize": 1, "FileData": "e A=="}),
            "not valid base64",
        ),
        (conversion_body({"FileName": "a", "FileSize": 1, "FileData": "é"}), "not valid base64"),
        (conversion_body({"FileName": "a", "FileSize": 1}), "Url"),
        (conversion_body({"FileName": "a", "FileSize": 1, "Url": "u"}), "FileId"),
        (
            conversion_body(
                {"FileName": "a", "FileSize": 1, "FileData": "eA=="}, ConversionCost="1"
            ),
            "ConversionCost",
        ),
        (
            conversion_body(
                {"FileName": "a", "FileSize": 1, "FileData": "eA=="}, ConversionTime=None
            ),
            "ConversionTime",
        ),
    ],
)
def test_unusable_bodies(body: bytes, fragment: str) -> None:
    with pytest.raises(InvalidResponseError, match=fragment) as caught:
        parse_conversion(body, "rid-9")
    assert caught.value.request_id == "rid-9"
    assert "rid-9" in str(caught.value)


# Usage and conversions


USAGE = {
    "Plan": "Free",
    "Included": 250,
    "Used": 12,
    "Remaining": 238,
    "Overage": False,
    "Running": 0,
    "ConcurrentLimit": 1,
    "MaxUploadMB": 10,
    "PeriodStart": "2026-11-01T00:00:00.000Z",
    "PeriodEnd": "2026-12-01T05:30:00+05:30",
}


def test_parse_usage() -> None:
    usage = parse_usage(json.dumps(USAGE).encode(), None)
    assert usage.plan == "Free"
    assert usage.remaining == 238
    assert usage.max_upload_mb == 10
    assert usage.period_start == datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert usage.period_start.tzinfo is timezone.utc
    assert usage.period_end == datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert usage.period_end.tzinfo is timezone.utc


def test_usage_remaining_may_be_null_and_naive_times_are_utc() -> None:
    usage = parse_usage(
        json.dumps(
            {**USAGE, "Remaining": None, "PeriodStart": "2026-11-01T00:00:00", "MaxUploadMB": 100.0}
        ).encode(),
        None,
    )
    assert usage.remaining is None
    assert usage.period_start == datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert usage.max_upload_mb == 100


@pytest.mark.parametrize(
    "change",
    [
        {"Plan": None},
        {"Used": "12"},
        {"Overage": "no"},
        {"PeriodEnd": "soon"},
        {"MaxUploadMB": 1.5},
    ],
)
def test_bad_usage(change: dict[str, Any]) -> None:
    with pytest.raises(InvalidResponseError):
        parse_usage(json.dumps({**USAGE, **change}).encode(), None)


def test_parse_conversions() -> None:
    body = json.dumps(
        [
            {
                "From": "docx",
                "To": "pdf",
                "Title": "DOCX to PDF",
                "Category": "Document",
                "Inputs": "one",
            }
        ]
    ).encode()
    [conversion] = parse_conversions(body, None)
    assert (conversion.from_format, conversion.to_format, conversion.inputs) == (
        "docx",
        "pdf",
        "one",
    )
    with pytest.raises(InvalidResponseError):
        parse_conversions(b"{}", None)
    with pytest.raises(InvalidResponseError):
        parse_conversions(b'[{"From": "docx"}]', None)
