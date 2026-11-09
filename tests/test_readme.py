"""Runs every Python block of README.md, so that each snippet is one that ran.

By default against the mock API (outputs are identity copies there); with ``-m live`` against
the real API (see the live_api fixture), with the sample files the README names, checking the
outputs it shows. The usage block runs against the mock only: the local API doesn't serve
/v2/usage.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from pdfconversionapi import PdfConversionApi

from .conftest import SAMPLES

README = Path(__file__).resolve().parent.parent / "README.md"


def read_blocks() -> list[tuple[str, str]]:
    """(id, code) of each Python block; the id names the README section it is in."""
    blocks: list[tuple[str, str]] = []
    section = "top"
    pattern = re.compile(r"^## ([^\n]+)$|^```python\n(.*?)^```", re.M | re.S)
    for match in pattern.finditer(README.read_text("utf-8")):
        if match.group(1):
            section = re.sub(r"[^a-z0-9]+", "-", match.group(1).lower()).strip("-")
        else:
            blocks.append((f"{len(blocks)}-{section}", match.group(2)))
    return blocks


BLOCKS = read_blocks()
IDS = [block_id for block_id, _ in BLOCKS]

FILES = {
    "report.docx": SAMPLES / "docx-to-pdf" / "input-quarterly-report.docx",
    "season-guide.pdf": SAMPLES / "pdf-to-jpg" / "input-season-guide.pdf",
    "invoice.pdf": SAMPLES / "merge" / "input-invoice.pdf",
    "terms.pdf": SAMPLES / "merge" / "input-terms.pdf",
    "timesheet.pdf": SAMPLES / "merge" / "input-timesheet.pdf",
    "proposal.pdf": SAMPLES / "watermark" / "input-proposal.pdf",
    "logo.png": SAMPLES / "png-to-jpg" / "input-mug-product.png",
}

# Output the README shows next to a snippet, keyed by text that identifies the snippet.
SHOWN = {
    '"Q3 report.docx"': ["Q3 report.pdf"],
    '{"ImageResolution": 72})\nfor file in result.files:': [
        "season-guide-1.jpg 65272\nseason-guide-2.jpg 66627"
    ],
    'file_name="october-billing"': ["october-billing.pdf 107308"],
    'file_name="guide"': ["['guide-2.jpg']"],
    '"dwg"': ["404 conversion from docx to dwg is not supported"],
}


def run_block(index: int, folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(folder)
    code = compile(BLOCKS[index][1], f"README.md block {IDS[index]}", "exec")
    namespace: dict[str, object] = {"__name__": "__main__"}
    try:
        exec(code, namespace)
    finally:
        # The snippets are short scripts that leave their client open; close it here.
        for value in namespace.values():
            if isinstance(value, PdfConversionApi):
                value._http.close()


def test_the_readme_has_blocks() -> None:
    assert len(BLOCKS) >= 10


@pytest.mark.parametrize("index", range(len(BLOCKS)), ids=IDS)
def test_block_against_the_mock(
    index: int, mock_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in FILES:
        (tmp_path / name).write_bytes(f"stand-in for {name}\n".encode())
    monkeypatch.setenv("PDFCONVERSIONAPI_KEY", "test-key")
    monkeypatch.setenv("PDFCONVERSIONAPI_BASE_URL", mock_url)
    run_block(index, tmp_path, monkeypatch)


@pytest.mark.live
@pytest.mark.parametrize("index", range(len(BLOCKS)), ids=IDS)
def test_block_against_the_local_api(
    index: int,
    live_api: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if ".usage()" in BLOCKS[index][1]:
        pytest.skip("the local API doesn't serve /v2/usage; this block runs against the mock")
    for name, source in FILES.items():
        shutil.copyfile(source, tmp_path / name)
    monkeypatch.setenv("PDFCONVERSIONAPI_KEY", live_api["key"])
    monkeypatch.setenv("PDFCONVERSIONAPI_BASE_URL", live_api["base_url"])
    run_block(index, tmp_path, monkeypatch)
    printed = capsys.readouterr().out
    for marker, outputs in SHOWN.items():
        if marker in BLOCKS[index][1]:
            for output in outputs:
                assert output in printed
