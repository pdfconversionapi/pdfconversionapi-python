"""The clients: settings, the key, headers, downloads, saving and sharing."""

from __future__ import annotations

import asyncio
import base64
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import pytest

import pdfconversionapi
from pdfconversionapi import (
    VERSION,
    APIConnectionError,
    AsyncPdfConversionApi,
    AuthenticationError,
    InvalidResponseError,
    MissingApiKeyError,
    NotFoundError,
    PdfConversionApi,
    StoredFileError,
    UploadFile,
)

KEY = "sk_test_0123456789abcdef"
BASE = "https://api.test"
FILE_ID = "0123456789abcdef0123456789abcdef"
STORED_URL = f"https://api.test/v2/d/{FILE_ID}/my%20report.pdf"


def inline(name: str, data: bytes) -> dict[str, Any]:
    return {
        "FileName": name,
        "FileExt": name.rsplit(".", 1)[-1],
        "FileSize": len(data),
        "FileData": base64.b64encode(data).decode(),
    }


def stored(name: str, size: int, url: str = STORED_URL) -> dict[str, Any]:
    return {"FileName": name, "FileExt": "pdf", "FileSize": size, "Url": url, "FileId": FILE_ID}


def converted(*files: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        json={"ConversionCost": 1, "ConversionTime": 0.5, "Files": list(files)},
        headers={"x-request-id": "rid"},
    )


class Recorder:
    """A transport that records requests and answers with ``respond``."""

    def __init__(self, respond: Any = None) -> None:
        self.requests: list[httpx.Request] = []
        self.bodies: list[bytes] = []
        self.respond = respond or (lambda request: converted(inline("a.pdf", b"x")))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(request.read())
        self.requests.append(request)
        response = self.respond(request)
        assert isinstance(response, httpx.Response)
        return response


def client_with(recorder: Recorder, **options: Any) -> PdfConversionApi:
    options.setdefault("api_key", KEY)
    options.setdefault("base_url", BASE)
    return PdfConversionApi(
        http_client=httpx.Client(transport=httpx.MockTransport(recorder)), **options
    )


# Settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    client = PdfConversionApi()
    assert client.base_url == "https://api.pdfconversionapi.com"
    assert client.timeout == 240
    assert client.connect_timeout == 10
    assert client.max_retries == 4
    assert client.max_retry_wait == 60
    assert (
        repr(client)
        == "PdfConversionApi(base_url='https://api.pdfconversionapi.com', api_key=<missing>)"
    )
    client.close()


def test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PDFCONVERSIONAPI_KEY", f"  {KEY}\n")
    monkeypatch.setenv("PDFCONVERSIONAPI_BASE_URL", "http://localhost:8110/")
    client = PdfConversionApi()
    assert client.base_url == "http://localhost:8110"
    assert client._settings.api_key == KEY
    assert KEY not in repr(client)
    assert "api_key=<set>" in repr(client)
    assert (
        PdfConversionApi(api_key="other-key-123456", base_url="https://x.test")._settings.api_key
        == "other-key-123456"
    )


def test_an_empty_key_is_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PDFCONVERSIONAPI_KEY", "")
    assert PdfConversionApi()._settings.api_key is None
    assert PdfConversionApi(api_key="  ")._settings.api_key is None


def test_a_key_that_cant_be_a_header_is_refused_without_echoing_it() -> None:
    with pytest.raises(ValueError, match="characters") as caught:
        PdfConversionApi(api_key="secret part\nmore")
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    "base_url",
    ["ftp://x.test", "api.test", "https://", "https://x.test/?a=1", "https://x.test/#f", ""],
)
def test_bad_base_urls(base_url: str) -> None:
    with pytest.raises(ValueError, match="base_url"):
        PdfConversionApi(base_url=base_url)


def test_base_url_with_a_path_prefix() -> None:
    recorder = Recorder()
    client_with(recorder, base_url="https://gw.test/pdf/").convert("TXT", "PDF", b"x")
    assert str(recorder.requests[0].url) == "https://gw.test/pdf/v2/convert/txt/to/pdf"


@pytest.mark.parametrize(
    ("option", "value", "error"),
    [
        ("timeout", 0, ValueError),
        ("timeout", -1, ValueError),
        ("timeout", float("nan"), ValueError),
        ("timeout", float("inf"), ValueError),
        ("timeout", "240", TypeError),
        ("timeout", True, TypeError),
        ("connect_timeout", 0, ValueError),
        ("max_retries", -1, ValueError),
        ("max_retries", 1.5, TypeError),
        ("max_retries", True, TypeError),
        ("max_retry_wait", -1, ValueError),
        ("api_key", 123, TypeError),
        ("base_url", 123, TypeError),
    ],
)
def test_bad_options(option: str, value: Any, error: type[Exception]) -> None:
    with pytest.raises(error):
        PdfConversionApi(**{option: value})


def test_max_retry_wait_may_be_zero() -> None:
    assert PdfConversionApi(max_retry_wait=0).max_retry_wait == 0


def test_the_http_client_must_match() -> None:
    with pytest.raises(TypeError, match="AsyncPdfConversionApi"):
        PdfConversionApi(http_client=httpx.AsyncClient())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="use PdfConversionApi"):
        AsyncPdfConversionApi(http_client=httpx.Client())  # type: ignore[arg-type]


def test_version() -> None:
    assert VERSION == pdfconversionapi.__version__ == "0.1.0"


# The key and headers


def test_calls_that_need_a_key_raise_before_sending() -> None:
    recorder = Recorder()
    client = client_with(recorder, api_key=None)
    with pytest.raises(MissingApiKeyError, match="PDFCONVERSIONAPI_KEY"):
        client.convert("txt", "pdf", b"x")
    with pytest.raises(MissingApiKeyError):
        client.convert_file("report.docx", "pdf")
    with pytest.raises(MissingApiKeyError):
        client.usage()
    with pytest.raises(MissingApiKeyError):
        client.delete_stored_file(FILE_ID)
    assert recorder.requests == []


def test_argument_errors_come_before_the_key_check() -> None:
    client = client_with(Recorder(), api_key=None)
    with pytest.raises(ValueError, match="from_format"):
        client.convert("../x", "pdf", b"x")


def test_conversion_headers() -> None:
    recorder = Recorder()
    client_with(recorder).convert("txt", "pdf", b"x")
    request = recorder.requests[0]
    assert request.method == "POST"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["accept"] == "application/json"
    assert re.fullmatch(
        r"pdfconversionapi-python/0\.1\.0 python/\d+\.\d+\.\d+\S*", request.headers["user-agent"]
    )
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert int(request.headers["content-length"]) == len(recorder.bodies[0])
    assert "transfer-encoding" not in request.headers
    assert request.url.query == b""


def test_conversions_and_downloads_send_no_key() -> None:
    info = [
        {
            "From": "docx",
            "To": "pdf",
            "Title": "DOCX to PDF",
            "Category": "Document",
            "Inputs": "one",
        }
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/info":
            return httpx.Response(200, json=info)
        return httpx.Response(200, content=b"%PDF")

    recorder = Recorder(respond)
    http = httpx.Client(
        transport=httpx.MockTransport(recorder),
        headers={"Authorization": "Bearer from-the-users-client"},
    )
    client = PdfConversionApi(api_key=KEY, base_url=BASE, http_client=http)
    assert client.conversions()[0].title == "DOCX to PDF"
    assert client.download(STORED_URL) == b"%PDF"
    for request in recorder.requests:
        assert "authorization" not in request.headers
    assert recorder.requests[1].headers.get("accept") != "application/json"
    keyless = client_with(Recorder(respond), api_key=None)
    assert keyless.conversions()[0].inputs == "one"
    assert keyless.download(STORED_URL) == b"%PDF"


def test_the_key_never_appears_in_errors() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed for {request.headers['authorization']}")

    client = client_with(Recorder(respond), max_retries=0)
    with pytest.raises(APIConnectionError) as caught:
        client.convert("txt", "pdf", b"x")
    assert KEY not in str(caught.value)
    assert "***" in str(caught.value)


def test_convert_file_uses_the_extension(tmp_path: Path) -> None:
    path = tmp_path / "Quarterly Report.DOCX"
    path.write_bytes(b"docx")
    recorder = Recorder()
    client_with(recorder).convert_file(path, "pdf", {"PageRange": "1"}, file_name="q3")
    assert recorder.requests[0].url.path == "/v2/convert/docx/to/pdf"
    body = recorder.bodies[0]
    assert b'filename="Quarterly Report.DOCX"' in body
    assert b'name="FileName"\r\n\r\nq3\r\n' in body
    with pytest.raises(ValueError, match="no extension"):
        client_with(recorder).convert_file(tmp_path / "README", "pdf")


# Results


def test_result_fields() -> None:
    recorder = Recorder(
        lambda request: converted(inline("a-1.jpg", b"one"), inline("a-2.jpg", b"two"))
    )
    result = client_with(recorder).convert("pdf", "jpg", b"%PDF")
    assert [f.name for f in result.files] == ["a-1.jpg", "a-2.jpg"]
    assert result.file.name == "a-1.jpg"
    assert result.file.ext == "jpg"
    assert result.file.size == 3
    assert not result.file.stored
    assert result.file.url is None
    assert result.file.file_id is None
    assert result.file.download() == b"one"
    assert result.conversion_cost == 1
    assert result.conversion_time == 0.5
    assert result.request_id == "rid"
    assert "a-1.jpg" in repr(result)
    assert repr(result.file) == "OutputFile(name='a-1.jpg', ext='jpg', size=3, stored=False)"


def test_save_and_save_all(tmp_path: Path) -> None:
    recorder = Recorder(
        lambda request: converted(inline("a-1.jpg", b"one"), inline("a-2.jpg", b"two"))
    )
    result = client_with(recorder).convert("pdf", "jpg", b"%PDF")
    out = tmp_path / "new" / "folder"
    (tmp_path / "new").mkdir()
    paths = result.save_all(out)
    assert paths == [out / "a-1.jpg", out / "a-2.jpg"]
    assert (out / "a-2.jpg").read_bytes() == b"two"
    (out / "a-1.jpg").write_bytes(b"old")
    result.save_all(out)
    assert (out / "a-1.jpg").read_bytes() == b"one"
    assert result.file.save(tmp_path) == tmp_path / "a-1.jpg"
    assert result.file.save(tmp_path / "renamed.jpg") == tmp_path / "renamed.jpg"
    assert result.file.download_to(str(tmp_path / "x.jpg")).read_bytes() == b"one"


@pytest.mark.parametrize(
    ("name", "written"), [("../../evil.txt", "evil.txt"), ("a\\b\\c.txt", "c.txt")]
)
def test_names_on_disk_are_the_files_own(tmp_path: Path, name: str, written: str) -> None:
    result = client_with(Recorder(lambda request: converted(inline(name, b"x")))).convert(
        "txt", "pdf", b"x"
    )
    assert result.save_all(tmp_path / "out") == [tmp_path / "out" / written]
    assert not (tmp_path / "evil.txt").exists()


@pytest.mark.parametrize("name", ["..", ".", "a/..", "a/", "nul\x00.txt"])
def test_unsafe_names_are_refused(tmp_path: Path, name: str) -> None:
    result = client_with(Recorder(lambda request: converted(inline(name, b"x")))).convert(
        "txt", "pdf", b"x"
    )
    with pytest.raises(InvalidResponseError, match="refusing"):
        result.save_all(tmp_path / "out")
    with pytest.raises(InvalidResponseError):
        result.file.save(tmp_path)


def stored_file_server(content: bytes) -> Recorder:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/v2/convert/"):
            return converted(stored("my report.pdf", len(content)))
        if request.method == "GET" and request.url.path == f"/v2/d/{FILE_ID}/my report.pdf":
            return httpx.Response(200, content=content, headers={"x-request-id": "d1"})
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(404, json={"Code": 404, "Message": "not found"})

    return Recorder(respond)


def test_stored_files(tmp_path: Path) -> None:
    recorder = stored_file_server(b"%PDF-1.7 stored")
    client = client_with(recorder)
    result = client.convert("docx", "pdf", b"docx", store_file=True)
    file = result.file
    assert file.stored
    assert file.url == STORED_URL
    assert file.file_id == FILE_ID
    with pytest.raises(StoredFileError, match="download"):
        _ = file.data
    assert file.download() == b"%PDF-1.7 stored"
    assert file.save(tmp_path) == tmp_path / "my report.pdf"
    assert (tmp_path / "my report.pdf").read_bytes() == b"%PDF-1.7 stored"
    assert result.save_all(tmp_path / "all") == [tmp_path / "all" / "my report.pdf"]
    assert (
        client.download_to(STORED_URL, tmp_path / "direct.pdf").read_bytes() == b"%PDF-1.7 stored"
    )
    folder = tmp_path / "into"
    folder.mkdir()
    assert client.download_to(STORED_URL, folder) == folder / "my report.pdf"
    assert client.delete_stored_file(FILE_ID.upper()) is True
    assert recorder.requests[-1].url.path == f"/v2/d/{FILE_ID}"
    assert recorder.requests[-1].headers["authorization"] == f"Bearer {KEY}"
    assert "stored=True" in repr(file)


def test_a_failed_download_leaves_no_file(tmp_path: Path) -> None:
    def broken() -> Any:
        yield b"partial"
        raise httpx.ReadError("reset")

    client = client_with(Recorder(lambda request: httpx.Response(200, content=broken())))
    target = tmp_path / "out.pdf"
    with pytest.raises(APIConnectionError):
        client.download_to(STORED_URL, target)
    assert not target.exists()
    missing = client_with(
        Recorder(lambda request: httpx.Response(404, json={"Code": 404, "Message": "not found"}))
    )
    with pytest.raises(NotFoundError):
        missing.download_to(STORED_URL, target)
    with pytest.raises(NotFoundError):
        missing.download(STORED_URL)
    assert not target.exists()


@pytest.mark.parametrize("url", ["/v2/d/x/y", "ftp://api.test/x", "not a url", ""])
def test_download_urls_must_be_absolute_http(url: str) -> None:
    client = client_with(Recorder())
    with pytest.raises(ValueError, match="url"):
        client.download(url)
    with pytest.raises(TypeError):
        client.download(None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("status", "outcome"),
    [(204, True), (200, True), (404, False), (401, AuthenticationError)],
)
def test_delete_stored_file(status: int, outcome: Any) -> None:
    body = {"Code": status, "Message": "m"} if status >= 400 else None
    client = client_with(Recorder(lambda request: httpx.Response(status, json=body)))
    if isinstance(outcome, bool):
        assert client.delete_stored_file(FILE_ID) is outcome
    else:
        with pytest.raises(outcome):
            client.delete_stored_file(FILE_ID)


def test_delete_needs_a_valid_id() -> None:
    client = client_with(Recorder())
    with pytest.raises(ValueError, match="32 hex"):
        client.delete_stored_file("../usage")


# Lifecycle and sharing


def test_owned_clients_are_closed_and_passed_ones_are_not() -> None:
    with PdfConversionApi() as client:
        http = client._http
    assert http.is_closed
    mine = httpx.Client()
    with PdfConversionApi(http_client=mine):
        pass
    assert not mine.is_closed
    mine.close()


@pytest.mark.anyio
async def test_async_lifecycle() -> None:
    async with AsyncPdfConversionApi() as client:
        http = client._http
    assert http.is_closed
    mine = httpx.AsyncClient()
    async with AsyncPdfConversionApi(http_client=mine):
        pass
    assert not mine.is_closed
    await mine.aclose()


def test_one_client_shared_by_threads(mock_url: str) -> None:
    with PdfConversionApi(api_key="test-key", base_url=mock_url) as client:

        def work(n: int) -> bytes:
            data = f"thread {n}\n".encode()
            return client.convert("txt", "pdf", UploadFile.from_bytes(data, f"t{n}.txt")).file.data

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(work, range(24)))
    assert results == [f"thread {n}\n".encode() for n in range(24)]


@pytest.mark.anyio
async def test_one_async_client_shared_by_tasks(mock_url: str, tmp_path: Path) -> None:
    async with AsyncPdfConversionApi(api_key="test-key", base_url=mock_url) as client:

        async def work(n: int) -> bytes:
            result = await client.convert(
                "txt", "pdf", UploadFile.from_bytes(f"task {n}".encode(), f"t{n}.txt")
            )
            return await result.file.download()

        results = await asyncio.gather(*(work(n) for n in range(24)))
        assert results == [f"task {n}".encode() for n in range(24)]

        stored_result = await client.convert("txt", "pdf", b"stored!", store_file=True)
        assert await stored_result.file.download() == b"stored!"
        paths = await stored_result.save_all(tmp_path / "out")
        assert paths[0].read_bytes() == b"stored!"
        assert (await stored_result.file.save(tmp_path / "copy.pdf")).read_bytes() == b"stored!"
        assert (await stored_result.file.download_to(tmp_path)).name == "file.pdf"
        assert stored_result.file.file_id is not None
        assert await client.delete_stored_file(stored_result.file.file_id) is True
        with pytest.raises(NotFoundError):
            await stored_result.file.download()
        with pytest.raises(NotFoundError):
            await client.download_to(stored_result.file.url or "", tmp_path / "gone.pdf")
        assert not (tmp_path / "gone.pdf").exists()
        usage = await client.usage()
        assert usage.plan == "Free"
        conversions = await client.conversions()
        assert len(conversions) == 5
        inline_result = await client.convert_file(tmp_path / "copy.pdf", "txt")
        assert (await inline_result.save_all(tmp_path / "inline"))[0].read_bytes() == b"stored!"
        assert await inline_result.file.download() == b"stored!"


def test_large_outputs_round_trip() -> None:
    data = bytes(range(256)) * 8192  # 2 MB
    client = client_with(Recorder(lambda request: converted(inline("big.bin", data))))
    assert client.convert("bin", "bin", b"x").file.data == data
