"""Request encoding: inputs, parameters, the multipart body and format names."""

from __future__ import annotations

import io
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from pdfconversionapi import UploadFile
from pdfconversionapi._core import check_file_id, check_format, convert_fields, format_number
from pdfconversionapi._inputs import own_name, path_extension, to_uploads
from pdfconversionapi._multipart import MultipartBody


def body_bytes(body: MultipartBody) -> bytes:
    return b"".join(body.chunks(lambda: None))


def parts(body: MultipartBody) -> list[tuple[str, bytes]]:
    """(headers, content) of each part."""
    raw = body_bytes(body)
    delimiter = f"--{body.boundary}".encode()
    assert raw.endswith(delimiter + b"--\r\n")
    pieces = raw.split(delimiter)[1:-1]
    result = []
    for piece in pieces:
        assert piece.startswith(b"\r\n")
        assert piece.endswith(b"\r\n")
        head, _, content = piece[2:-2].partition(b"\r\n\r\n")
        result.append((head.decode("utf-8"), content))
    return result


# Inputs


def test_a_str_is_a_path(tmp_path: Path) -> None:
    path = tmp_path / "report.docx"
    path.write_bytes(b"docx")
    [upload] = to_uploads(str(path))
    assert upload.name == "report.docx"
    assert b"".join(upload._chunks(upload._content_size())) == b"docx"


def test_a_missing_path_raises_before_sending(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        MultipartBody(convert_fields(str(tmp_path / "missing.pdf"), None, None, False))


@pytest.mark.parametrize(
    ("name", "sent"),
    [
        ("reports/q3.docx", "q3.docx"),
        ("C:\\Users\\me\\q3.docx", "q3.docx"),
        ("../../My Photo (1).png", "My Photo (1).png"),
        ("plain.txt", "plain.txt"),
        ("folder/", "file"),
        ('say "hi".txt', "say _hi_.txt"),
        ('"', "_"),
    ],
)
def test_only_the_files_own_name_is_sent(name: str, sent: str) -> None:
    assert UploadFile.from_bytes(b"x", name).name == sent


def test_bytes_without_a_name_are_sent_as_file() -> None:
    assert UploadFile.from_bytes(b"x").name == "file"
    assert to_uploads(bytearray(b"x"))[0].name == "file"
    assert to_uploads(memoryview(b"x"))[0].name == "file"


def test_an_open_file_is_sent_under_its_own_name(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_bytes(b"hello")
    with open(path, "rb") as f:
        [upload] = to_uploads(f)
        assert upload.name == "notes.txt"
    assert UploadFile.from_stream(io.BytesIO(b"x")).name == "file"


def test_a_seekable_stream_is_rewound_for_each_attempt() -> None:
    stream = io.BytesIO(b"skip|payload")
    stream.seek(5)
    upload = UploadFile.from_stream(stream, "a.txt")
    size = upload._content_size()
    assert size == 7
    first = b"".join(upload._chunks(size))
    second = b"".join(upload._chunks(size))
    assert first == second == b"payload"


class OneShot:
    """A stream that can be read once and can't seek."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self, size: int = -1, /) -> bytes:
        data, self._data = self._data, b""
        return data


def test_a_stream_that_cant_seek_is_read_once_into_memory() -> None:
    upload = UploadFile.from_stream(OneShot(b"abc"), "a.txt")
    assert b"".join(upload._chunks(3)) == b"abc"
    assert b"".join(upload._chunks(3)) == b"abc"


def test_a_text_stream_is_refused() -> None:
    with pytest.raises(TypeError, match="binary mode"):
        UploadFile.from_stream(io.StringIO("text"), "a.txt")  # type: ignore[arg-type]


def test_a_file_that_shrinks_during_the_upload_raises(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_bytes(b"12345")
    upload = UploadFile.from_path(path)
    size = upload._content_size()
    path.write_bytes(b"12")
    with pytest.raises(OSError, match="shorter"):
        b"".join(upload._chunks(size))


def test_files_forms() -> None:
    assert to_uploads(None) == []
    assert to_uploads([]) == []
    assert [u.name for u in to_uploads([b"a", UploadFile.from_bytes(b"b", "b.txt")])] == [
        "file",
        "b.txt",
    ]
    assert len(to_uploads((b"a", b"b"))) == 2
    with pytest.raises(TypeError):
        to_uploads(42)
    with pytest.raises(TypeError):
        to_uploads(x for x in [b"a"])
    with pytest.raises(TypeError):
        to_uploads([b"a", 42])


def test_upload_file_needs_a_factory() -> None:
    with pytest.raises(TypeError, match="from_path"):
        UploadFile()
    with pytest.raises(TypeError):
        UploadFile.from_bytes("text")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        UploadFile.from_path(b"bytes")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="empty"):
        UploadFile.from_path("")


def test_own_name() -> None:
    assert own_name("a/b\\c.txt") == "c.txt"
    assert own_name("c.txt") == "c.txt"


# Parameters and fields


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (72, "72"),
        (-3, "-3"),
        (1.5, "1.5"),
        (72.0, "72"),
        (0.1, "0.1"),
        (1e-7, "0.0000001"),
        (1e20, "100000000000000000000"),
        (Decimal("1.50"), "1.5"),
        (Decimal("1E+2"), "100"),
        (Decimal("3"), "3"),
    ],
)
def test_numbers_are_plain_decimal(value: Any, text: str) -> None:
    assert format_number(value) == text


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), Decimal("NaN"), Decimal("-Infinity")]
)
def test_numbers_must_be_finite(value: Any) -> None:
    with pytest.raises(ValueError, match="finite"):
        format_number(value)


def test_fields_in_order() -> None:
    fields = convert_fields(
        [b"a", b"b"],
        {"PageRange": "1-3", "Dpi": 72, "Gray": True, "Multi": False, "Empty": None},
        None,
        False,
    )
    assert [(name, value if isinstance(value, str) else "<file>") for name, value in fields] == [
        ("Files", "<file>"),
        ("Files", "<file>"),
        ("PageRange", "1-3"),
        ("Dpi", "72"),
        ("Gray", "true"),
        ("Multi", "false"),
    ]


def test_one_file_is_sent_as_file_and_none_as_nothing() -> None:
    assert [name for name, _ in convert_fields(b"a", None, None, False)] == ["File"]
    assert [name for name, _ in convert_fields([b"a"], None, None, False)] == ["File"]
    assert convert_fields(None, {"Data": "x"}, None, False) == [("Data", "x")]


def test_typed_options_win_over_params() -> None:
    fields = convert_fields(
        b"a",
        {"filename": "from-params", "STOREFILE": "false", "Other": "x"},
        "typed",
        True,
    )
    texts = [(name, value) for name, value in fields if isinstance(value, str)]
    assert texts == [("Other", "x"), ("FileName", "typed"), ("StoreFile", "true")]


def test_params_keep_filename_and_storefile_when_the_options_are_not_set() -> None:
    fields = convert_fields(b"a", {"FileName": "mine", "StoreFile": True}, None, False)
    texts = [(name, value) for name, value in fields if isinstance(value, str)]
    assert texts == [("FileName", "mine"), ("StoreFile", "true")]


def test_file_values_are_named_file_fields_and_strings_are_text(tmp_path: Path) -> None:
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"png")
    fields = convert_fields(b"pdf", {"ImageFile": logo, "Text": "logo.png"}, None, False)
    image = fields[1][1]
    assert isinstance(image, UploadFile)
    assert image.name == "logo.png"
    assert fields[2] == ("Text", "logo.png")


def test_bad_params_are_refused() -> None:
    with pytest.raises(TypeError, match="mapping"):
        convert_fields(b"a", [("A", "b")], None, False)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="names"):
        convert_fields(b"a", {1: "b"}, None, False)  # type: ignore[dict-item]
    with pytest.raises(TypeError, match="parameter 'A'"):
        convert_fields(b"a", {"A": [1, 2]}, None, False)
    with pytest.raises(TypeError):
        convert_fields(b"a", None, 5, False)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        convert_fields(b"a", None, None, "yes")  # type: ignore[arg-type]


# The multipart body


def test_body_layout_and_length() -> None:
    body = MultipartBody(
        convert_fields(
            UploadFile.from_bytes(b"\x00\xffdata", "a.pdf"), {"Dpi": 72, "Note": "é"}, None, False
        )
    )
    raw = body_bytes(body)
    assert len(raw) == body.content_length
    assert body.content_type == f"multipart/form-data; boundary={body.boundary}"
    assert re.fullmatch(r"[0-9a-f]{32}", body.boundary)
    assert parts(body) == [
        (
            'Content-Disposition: form-data; name="File"; filename="a.pdf"\r\n'
            "Content-Type: application/octet-stream",
            b"\x00\xffdata",
        ),
        ('Content-Disposition: form-data; name="Dpi"', b"72"),
        ('Content-Disposition: form-data; name="Note"', "é".encode()),
    ]


def test_file_names_are_raw_utf8_with_quotes_and_newlines_as_underscores() -> None:
    upload = UploadFile.from_bytes(b"x", 'Résumé "final"\r\n(1).txt')
    assert upload.name == "Résumé _final___(1).txt"
    body = MultipartBody([("File", upload)])
    head = parts(body)[0][0]
    assert 'filename="Résumé _final___(1).txt"' in head
    assert "filename*" not in head
    raw = body_bytes(body)
    assert "Résumé".encode() in raw


def test_field_names_are_escaped_too() -> None:
    body = MultipartBody([('a"b\nc', "v")])
    assert parts(body)[0][0] == 'Content-Disposition: form-data; name="a%22b%0Ac"'


def test_an_empty_form_is_just_the_closing_boundary() -> None:
    body = MultipartBody([])
    assert body_bytes(body) == f"--{body.boundary}--\r\n".encode()
    assert body.content_length == len(body_bytes(body))


def test_a_path_is_read_again_for_each_body(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_bytes(b"one")
    fields = convert_fields(str(path), None, None, False)
    first = MultipartBody(fields)
    assert parts(first)[0][1] == b"one"
    path.write_bytes(b"two!")
    second = MultipartBody(fields)
    assert parts(second)[0][1] == b"two!"
    assert second.content_length == first.content_length + 1


def test_the_check_runs_between_chunks() -> None:
    calls = []
    body = MultipartBody([("File", UploadFile.from_bytes(b"x" * 200_000, "a"))])
    list(body.chunks(lambda: calls.append(1)))
    assert len(calls) > 4


# Format names, ids and paths


@pytest.mark.parametrize("value", ["docx", "PDF", "image-watermark", "a" * 20, "7z"])
def test_valid_formats_are_lowercased(value: str) -> None:
    assert check_format(value, "from_format") == value.lower()


@pytest.mark.parametrize(
    "value", ["", "a" * 21, "../etc", "pdf/to", "dócx", "pdf ", "pdf\n", "pd_f", "pdf.x"]
)
def test_invalid_formats_are_refused(value: str) -> None:
    with pytest.raises(ValueError, match="1 to 20 letters"):
        check_format(value, "from_format")


def test_format_must_be_a_str() -> None:
    with pytest.raises(TypeError):
        check_format(None, "to_format")


def test_file_ids() -> None:
    assert check_file_id("ABCDEF0123456789abcdef0123456789") == "abcdef0123456789abcdef0123456789"
    for bad in ["", "abc", "g" * 32, "a" * 33, "a" * 31 + "\n"]:
        with pytest.raises(ValueError, match="32 hex"):
            check_file_id(bad)
    with pytest.raises(TypeError):
        check_file_id(123)


@pytest.mark.parametrize(
    ("path", "ext"),
    [
        ("report.docx", "docx"),
        ("a/b/Photo.JPG", "jpg"),
        ("archive.tar.gz", "gz"),
        (Path("x.pdf"), "pdf"),
    ],
)
def test_path_extension(path: Any, ext: str) -> None:
    assert path_extension(path) == ext


@pytest.mark.parametrize("path", ["README", ".bashrc", "folder/", "name."])
def test_a_path_without_an_extension_is_refused(path: str) -> None:
    with pytest.raises(ValueError, match="no extension"):
        path_extension(path)
