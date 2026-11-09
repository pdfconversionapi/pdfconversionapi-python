"""Runs every case of ../conformance/cases.json against the mock API, with the sync and the
async client. The case format is described in ../conformance/README.md."""

from __future__ import annotations

import inspect
import io
import json
import uuid
from collections.abc import Awaitable
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar, Union

import httpx
import pytest

import pdfconversionapi
from pdfconversionapi import (
    APIConnectionError,
    APITimeoutError,
    AsyncConversionResult,
    AsyncPdfConversionApi,
    ConversionResult,
    PdfConversionApi,
    StoredFileError,
    UploadFile,
)

from .conftest import CONFORMANCE_DIR, REQUIRE_CONFORMANCE

T = TypeVar("T")
AnyClient = Union[PdfConversionApi, AsyncPdfConversionApi]
AnyResult = Union[ConversionResult, AsyncConversionResult]

CASES_FILE = CONFORMANCE_DIR / "cases.json"
SUITE: dict[str, Any] = json.loads(CASES_FILE.read_text("utf-8")) if CASES_FILE.exists() else {}
CASES: list[dict[str, Any]] = SUITE.get("cases", [])
FIXTURES = CONFORMANCE_DIR / "fixtures"

KNOWN_EXPECTATIONS = {
    "result",
    "echo",
    "error",
    "attempts",
    "min_sleeps",
    "save_all",
    "conversions",
    "usage",
    "deleted",
    "download_fixture",
    "data_access_error",
    "first_delete",
    "second_delete",
    "download_after_delete_error",
}

ERROR_TYPES: dict[str, tuple[type[BaseException], ...]] = {
    "ArgumentError": (ValueError, TypeError),
    "ConnectionError": (APIConnectionError,),
    "TimeoutError": (APITimeoutError,),
}


def error_type(name: str) -> tuple[type[BaseException], ...]:
    if name in ERROR_TYPES:
        return ERROR_TYPES[name]
    found = getattr(pdfconversionapi, name, None)
    assert isinstance(found, type), f"no error class named {name}"
    return (found,)


async def done(value: T | Awaitable[T]) -> T:
    """The value of a sync call, or the awaited value of an async one."""
    if inspect.isawaitable(value):
        return await value
    return value


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def make_input(descriptor: dict[str, Any], *, as_param: bool = False) -> Any:
    name = descriptor.get("name")
    if "path" in descriptor:
        path = FIXTURES / descriptor["path"]
        # A str is always a path for inputs; a parameter's str is text, so pass a Path there.
        return path if as_param else str(path)
    if "bytes" in descriptor:
        data = fixture_bytes(descriptor["bytes"])
        return data if name is None else UploadFile.from_bytes(data, name)
    if "bytes_utf8" in descriptor:
        data = descriptor["bytes_utf8"].encode("utf-8")
        return data if name is None else UploadFile.from_bytes(data, name)
    if "stream" in descriptor:
        stream = io.BytesIO(fixture_bytes(descriptor["stream"]))
        return stream if name is None else UploadFile.from_stream(stream, name)
    raise AssertionError(f"unknown input descriptor {descriptor}")


class Recorder:
    def __init__(self) -> None:
        self.waits: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)

    async def async_sleep(self, seconds: float) -> None:
        self.waits.append(seconds)


def make_client(case: dict[str, Any], mode: str, mock_url: str, recorder: Recorder) -> AnyClient:
    options = case.get("client", {})
    key = options.get("api_key", SUITE["key"]) if "api_key" in options else SUITE["key"]
    kwargs: dict[str, Any] = {
        "api_key": key,
        "base_url": mock_url + options.get("base_url_suffix", ""),
    }
    if "max_retries" in options:
        kwargs["max_retries"] = options["max_retries"]
    if "timeout_seconds" in options:
        kwargs["timeout"] = options["timeout_seconds"]
    if mode == "sync":
        return PdfConversionApi(sleep=recorder.sleep, **kwargs)
    return AsyncPdfConversionApi(sleep=recorder.async_sleep, **kwargs)


async def close(client: AnyClient) -> None:
    if isinstance(client, AsyncPdfConversionApi):
        await client.aclose()
    else:
        client.close()


def attempts(mock_url: str, mock_id: str) -> int:
    response = httpx.get(f"{mock_url}/__mock/attempts/{mock_id}")
    count = response.json()["attempts"]
    assert isinstance(count, int)
    return count


async def convert(client: AnyClient, case: dict[str, Any], mock_id: str) -> AnyResult:
    params: dict[str, Any] = {}
    for name, value in case.get("params", {}).items():
        params[name] = make_input(value, as_param=True) if isinstance(value, dict) else value
    params["MockId"] = mock_id
    files: Any = None
    if "files" in case:
        inputs = [make_input(d) for d in case["files"]]
        files = inputs[0] if len(inputs) == 1 else inputs
    options = case.get("options", {})
    return await done(
        client.convert(
            case["from"],
            case["to"],
            files,
            params,
            file_name=options.get("file_name"),
            store_file=options.get("store_file", False),
        )
    )


def check_files(result: AnyResult, expected: list[dict[str, Any]]) -> None:
    files = result.files
    assert len(files) == len(expected), [f.name for f in files]
    for output, want in zip(files, expected):
        for key in ("name", "ext", "size"):
            if key in want:
                assert getattr(output, key) == want[key], (key, output)
        if "data_fixture" in want:
            assert output.data == fixture_bytes(want["data_fixture"])
        if "data_utf8" in want:
            assert output.data == want["data_utf8"].encode("utf-8")
        if want.get("stored"):
            assert output.stored
            assert output.url
            assert output.file_id
            with pytest.raises(StoredFileError):
                _ = output.data


def check_result(result: AnyResult, expected: dict[str, Any]) -> None:
    if "conversion_cost" in expected:
        assert result.conversion_cost == expected["conversion_cost"]
    if expected.get("request_id"):
        assert isinstance(result.request_id, str)
        assert result.request_id
    if "files" in expected:
        check_files(result, expected["files"])


async def check_echo(result: AnyResult, expected: dict[str, Any]) -> None:
    echo = json.loads(await done(result.file.download()))
    headers = echo["headers"]
    for key in ("method", "path", "query"):
        if key in expected:
            assert echo[key] == expected[key], key
    if "authorization" in expected:
        assert headers["authorization"] == expected["authorization"]
    if "user_agent_prefix" in expected:
        assert headers["user-agent"].startswith(expected["user_agent_prefix"])
    if "content_type_prefix" in expected:
        assert headers["content-type"].startswith(expected["content_type_prefix"])
    fields = echo["fields"]
    position = 0
    for want in expected.get("fields", []):
        while position < len(fields) and not all(
            fields[position].get(k) == v for k, v in want.items()
        ):
            position += 1
        assert position < len(fields), f"field {want} missing or out of order in {fields}"
        position += 1
    for name in expected.get("absent_fields", []):
        assert all(field["name"] != name for field in fields), f"{name} was sent"


def check_error(error: BaseException, expected: dict[str, Any]) -> None:
    assert isinstance(error, error_type(expected["type"])), repr(error)
    if "status" in expected:
        assert getattr(error, "status", None) == expected["status"]
    if "message" in expected:
        assert getattr(error, "message", None) == expected["message"]
    if "message_contains" in expected:
        assert expected["message_contains"] in getattr(error, "message", "")
    if "retry_after" in expected:
        assert getattr(error, "retry_after", None) == expected["retry_after"]
    if "request_id" in expected:
        request_id = getattr(error, "request_id", None)
        if expected["request_id"]:
            assert isinstance(request_id, str)
            assert request_id
        else:
            assert request_id is None


async def check_save_all(result: AnyResult, expected: dict[str, str], folder: Path) -> None:
    written = await done(result.save_all(folder))
    assert sorted(p.name for p in written) == sorted(expected)
    assert sorted(p.name for p in folder.iterdir()) == sorted(expected)
    for name, text in expected.items():
        assert (folder / name).read_bytes() == text.encode("utf-8")


async def stored_file_flow(client: AnyClient, result: AnyResult, expect: dict[str, Any]) -> None:
    stored = result.file
    with pytest.raises(error_type(expect["data_access_error"])):
        _ = stored.data
    assert await done(stored.download()) == fixture_bytes(expect["download_fixture"])
    assert stored.file_id is not None
    assert await done(client.delete_stored_file(stored.file_id)) is expect["first_delete"]
    assert await done(client.delete_stored_file(stored.file_id)) is expect["second_delete"]
    with pytest.raises(error_type(expect["download_after_delete_error"])):
        await done(stored.download())


async def run_case(case: dict[str, Any], mode: str, mock_url: str, tmp_path: Path) -> None:
    expect: dict[str, Any] = case["expect"]
    unknown = set(expect) - KNOWN_EXPECTATIONS
    assert not unknown, f"the runner doesn't know the expectations {unknown}"
    recorder = Recorder()
    client = make_client(case, mode, mock_url, recorder)
    mock_id = f"{case['id']}-{mode}-{uuid.uuid4().hex[:12]}"
    call = case["call"]
    try:
        outcome: Any = None
        error: BaseException | None = None
        try:
            if call in ("convert", "stored_file_flow"):
                outcome = await convert(client, case, mock_id)
            elif call == "usage":
                outcome = await done(client.usage())
            elif call == "conversions":
                outcome = await done(client.conversions())
            elif call == "delete_stored_file":
                outcome = await done(client.delete_stored_file(case["file_id"]))
            else:
                raise AssertionError(f"unknown call {call}")
        except (pdfconversionapi.PdfConversionApiError, ValueError, TypeError) as caught:
            error = caught

        if "error" in expect:
            assert error is not None, f"expected {expect['error']['type']}, got {outcome!r}"
            check_error(error, expect["error"])
        elif error is not None:
            raise error

        if "result" in expect:
            check_result(outcome, expect["result"])
        if "echo" in expect:
            await check_echo(outcome, expect["echo"])
        if "save_all" in expect:
            await check_save_all(outcome, expect["save_all"], tmp_path / "out")
        if "conversions" in expect:
            want = expect["conversions"]
            assert len(outcome) == want["count"]
            first = outcome[0]
            names = {"from": "from_format", "to": "to_format"}
            for key, value in want.get("first", {}).items():
                assert getattr(first, names.get(key, key)) == value, key
        if "usage" in expect:
            for key, value in expect["usage"].items():
                actual = getattr(outcome, key)
                if isinstance(actual, datetime):
                    assert actual == datetime.fromisoformat(value.replace("Z", "+00:00")), key
                else:
                    assert actual == value, key
        if "deleted" in expect:
            assert outcome is expect["deleted"]
        if call == "stored_file_flow":
            await stored_file_flow(client, outcome, expect)
        if "attempts" in expect:
            assert attempts(mock_url, mock_id) == expect["attempts"]
        if "min_sleeps" in expect:
            assert len(recorder.waits) == len(expect["min_sleeps"]), recorder.waits
            for waited, minimum in zip(recorder.waits, expect["min_sleeps"]):
                assert waited >= minimum, recorder.waits
    finally:
        await close(client)


def test_the_suite_was_found() -> None:
    if not CASES_FILE.exists() and not REQUIRE_CONFORMANCE:
        pytest.skip(f"the conformance suite is not at {CONFORMANCE_DIR}")
    assert CASES, f"no cases in {CASES_FILE}"
    assert len({case["id"] for case in CASES}) == len(CASES)


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
async def test_case(case: dict[str, Any], mode: str, mock_url: str, tmp_path: Path) -> None:
    await run_case(case, mode, mock_url, tmp_path)
