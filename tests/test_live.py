"""Live tests against the real API (SPEC section 12.3).

They read PDFCONVERSIONAPI_LOCAL_BASE_URL and PDFCONVERSIONAPI_LOCAL_KEY from
../conformance/.local-api.env (the API running locally), else PDFCONVERSIONAPI_LIVE_BASE_URL and
PDFCONVERSIONAPI_KEY from the environment (the release workflow's production run), and are
skipped without either. The input files are in ../conformance/samples. They are not part of the
default run: select them with ``pytest -m live tests/test_live.py``. One run uses 9 conversions.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from pdfconversionapi import (
    AsyncPdfConversionApi,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PdfConversionApi,
    StoredFileError,
    UploadFile,
)

from .conftest import SAMPLES

pytestmark = pytest.mark.live

DOCX = SAMPLES / "docx-to-pdf" / "input-quarterly-report.docx"
SEASON_GUIDE = SAMPLES / "pdf-to-jpg" / "input-season-guide.pdf"
MERGE = [SAMPLES / "merge" / f"input-{name}.pdf" for name in ("invoice", "terms", "timesheet")]
LOGO = SAMPLES / "png-to-jpg" / "input-mug-product.png"
PROPOSAL = SAMPLES / "watermark" / "input-proposal.pdf"


@pytest.fixture
def client(live_api: dict[str, str]) -> Iterator[PdfConversionApi]:
    with PdfConversionApi(api_key=live_api["key"], base_url=live_api["base_url"]) as client:
        yield client


def test_docx_to_pdf(client: PdfConversionApi, tmp_path: Path) -> None:
    result = client.convert_file(DOCX, "pdf")
    assert [f.name for f in result.files] == ["input-quarterly-report.pdf"]
    assert result.file.ext == "pdf"
    assert result.file.data.startswith(b"%PDF")
    assert result.file.size == len(result.file.data)
    assert result.conversion_cost == 1
    assert result.conversion_time > 0
    assert result.request_id
    [path] = result.save_all(tmp_path / "out")
    assert path == tmp_path / "out" / "input-quarterly-report.pdf"
    assert path.read_bytes() == result.file.data


def test_pdf_to_jpg_gives_two_files(client: PdfConversionApi) -> None:
    with SEASON_GUIDE.open("rb") as stream:
        result = client.convert("pdf", "jpg", stream, {"ImageResolution": 72})
    assert [f.name for f in result.files] == [
        "input-season-guide-1.jpg",
        "input-season-guide-2.jpg",
    ]
    for file in result.files:
        assert file.ext == "jpg"
        assert file.data.startswith(b"\xff\xd8\xff")


def test_merge_three_pdfs(client: PdfConversionApi) -> None:
    result = client.convert("pdf", "merge", [str(p) for p in MERGE], file_name="october-billing")
    assert [f.name for f in result.files] == ["october-billing.pdf"]
    assert result.file.data.startswith(b"%PDF")
    assert result.file.size > max(p.stat().st_size for p in MERGE)


def test_unicode_file_name_round_trip(client: PdfConversionApi) -> None:
    result = client.convert(
        "docx", "pdf", UploadFile.from_bytes(DOCX.read_bytes(), 'Résumé "final" (1).docx')
    )
    assert result.file.name == "Résumé _final_ _1_.pdf"


def test_named_file_image_watermark(client: PdfConversionApi) -> None:
    result = client.convert("pdf", "image-watermark", PROPOSAL, {"ImageFile": LOGO, "Opacity": 30})
    assert result.file.name == "input-proposal.pdf"
    assert result.file.data.startswith(b"%PDF")


def test_no_file_qrcode(client: PdfConversionApi) -> None:
    result = client.convert("data", "qrcode", params={"Data": "https://pdfconversionapi.com"})
    assert result.file.name == "qrcode.svg"
    assert b"<svg" in result.file.data


def test_store_download_delete(client: PdfConversionApi, tmp_path: Path) -> None:
    result = client.convert_file(DOCX, "pdf", store_file=True)
    file = result.file
    assert file.stored
    assert file.url
    assert file.file_id
    assert len(file.file_id) == 32
    with pytest.raises(StoredFileError):
        _ = file.data
    data = file.download()
    assert data.startswith(b"%PDF")
    assert len(data) == file.size
    path = file.save(tmp_path)
    assert path == tmp_path / "input-quarterly-report.pdf"
    assert path.read_bytes() == data
    assert client.delete_stored_file(file.file_id) is True
    assert client.delete_stored_file(file.file_id) is False
    with pytest.raises(NotFoundError):
        file.download()


def test_unsupported_pair_is_404(client: PdfConversionApi) -> None:
    with pytest.raises(NotFoundError) as caught:
        client.convert_file(DOCX, "dwg")
    assert caught.value.status == 404
    assert caught.value.message == "conversion from docx to dwg is not supported"
    assert caught.value.request_id


def test_wrong_key_is_401(live_api: dict[str, str]) -> None:
    with (
        PdfConversionApi(api_key="wrong-key-0123456789", base_url=live_api["base_url"]) as client,
        pytest.raises(AuthenticationError) as caught,
    ):
        client.convert_file(DOCX, "pdf")
    assert caught.value.status == 401
    assert "valid API key" in caught.value.message
    assert "wrong-key" not in str(caught.value)


def test_bad_page_range_is_400(client: PdfConversionApi) -> None:
    with pytest.raises(BadRequestError) as caught:
        client.convert_file(SEASON_GUIDE, "jpg", {"PageRange": "3-1"})
    assert caught.value.message == "PageRange '3-1' is not valid (example: 1-3,5)"
    assert caught.value.request_id


def test_conversions_list(client: PdfConversionApi) -> None:
    conversions = client.conversions()
    pairs = {(c.from_format, c.to_format) for c in conversions}
    assert ("docx", "pdf") in pairs
    assert ("pdf", "merge") in pairs
    assert {c.inputs for c in conversions} <= {"one", "many", "optional"}


@pytest.mark.anyio
async def test_async_convert_and_store(live_api: dict[str, str], tmp_path: Path) -> None:
    async with AsyncPdfConversionApi(
        api_key=live_api["key"], base_url=live_api["base_url"]
    ) as client:
        result = await client.convert_file(DOCX, "pdf")
        assert result.file.data.startswith(b"%PDF")
        stored = await client.convert(
            "pdf", "jpg", SEASON_GUIDE, {"ImageResolution": 72}, store_file=True
        )
        paths = await stored.save_all(tmp_path / "pages")
        assert [p.name for p in paths] == ["input-season-guide-1.jpg", "input-season-guide-2.jpg"]
        for file, path in zip(stored.files, paths):
            assert path.stat().st_size == file.size
            assert file.file_id is not None
            assert await client.delete_stored_file(file.file_id) is True


def test_samples_exist() -> None:
    for path in [DOCX, SEASON_GUIDE, *MERGE, LOGO, PROPOSAL]:
        assert path.exists(), path
